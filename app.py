from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Flask, flash, g, redirect, render_template, request, url_for

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "works.db"

# Формула представлена как 4 четверти зубов человека.
FORMULA_QUADRANTS = [
    {"key": "q1", "title": "Верхняя правая (1 четверть)", "teeth": [18, 17, 16, 15, 14, 13, 12, 11]},
    {"key": "q2", "title": "Верхняя левая (2 четверть)", "teeth": [21, 22, 23, 24, 25, 26, 27, 28]},
    {"key": "q4", "title": "Нижняя правая (4 четверть)", "teeth": [48, 47, 46, 45, 44, 43, 42, 41]},
    {"key": "q3", "title": "Нижняя левая (3 четверть)", "teeth": [31, 32, 33, 34, 35, 36, 37, 38]},
]
ALL_TEETH = [str(tooth) for quadrant in FORMULA_QUADRANTS for tooth in quadrant["teeth"]]
UPPER_TEETH = {str(tooth) for tooth in FORMULA_QUADRANTS[0]["teeth"] + FORMULA_QUADRANTS[1]["teeth"]}
LOWER_TEETH = {str(tooth) for tooth in FORMULA_QUADRANTS[2]["teeth"] + FORMULA_QUADRANTS[3]["teeth"]}

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-key-change-me"


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_error: Exception | None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS works (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room TEXT NOT NULL,
            doctor TEXT NOT NULL,
            patient TEXT NOT NULL,
            formula TEXT NOT NULL,
            upper_full_removable INTEGER NOT NULL DEFAULT 0,
            lower_full_removable INTEGER NOT NULL DEFAULT 0,
            work_type TEXT NOT NULL,
            note TEXT,
            received_date TEXT NOT NULL,
            submission_date TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    columns = {
        row["name"]
        for row in db.execute("PRAGMA table_info(works)").fetchall()
    }
    if "received_date" not in columns:
        db.execute("ALTER TABLE works ADD COLUMN received_date TEXT NOT NULL DEFAULT ''")
    if "upper_full_removable" not in columns:
        db.execute("ALTER TABLE works ADD COLUMN upper_full_removable INTEGER NOT NULL DEFAULT 0")
    if "lower_full_removable" not in columns:
        db.execute("ALTER TABLE works ADD COLUMN lower_full_removable INTEGER NOT NULL DEFAULT 0")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS fittings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_id INTEGER NOT NULL,
            sent_date TEXT NOT NULL,
            returned_date TEXT,
            created_at TEXT NOT NULL,
            returned_at TEXT,
            FOREIGN KEY (work_id) REFERENCES works (id)
        )
        """
    )
    db.commit()


@app.before_request
def ensure_db() -> None:
    init_db()


def parse_formula(formula_text: str) -> set[str]:
    return {item.strip() for item in formula_text.split(",") if item.strip()}


def parse_tags(raw_text: str) -> list[str]:
    parts = raw_text.replace(";", ",").split(",")
    tags: list[str] = []
    for item in parts:
        tag = item.strip().lstrip("#")
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def serialize_tags(raw_text: str) -> str:
    return ",".join(parse_tags(raw_text))


def parse_iso_date(value: str, field_title: str) -> tuple[str | None, str | None]:
    date_value = (value or "").strip()
    if not date_value:
        return None, f"Поле '{field_title}' обязательно."
    try:
        datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError:
        return None, f"Поле '{field_title}' должно быть в формате ГГГГ-ММ-ДД."
    return date_value, None


@app.route("/")
def works_list() -> str:
    db = get_db()
    selected_rooms = [item.strip() for item in request.args.getlist("room") if item.strip()]
    selected_doctors = [item.strip() for item in request.args.getlist("doctor") if item.strip()]
    selected_patients = [item.strip() for item in request.args.getlist("patient") if item.strip()]
    filters = {
        "room": selected_rooms,
        "doctor": selected_doctors,
        "patient": selected_patients,
        "received_date_from": request.args.get("received_date_from", "").strip(),
        "received_date_to": request.args.get("received_date_to", "").strip(),
        "submission_date_from": request.args.get("submission_date_from", "").strip(),
        "submission_date_to": request.args.get("submission_date_to", "").strip(),
        "status": (
            [s for s in request.args.getlist("status") if s in {"in_progress", "fitting", "done"}]
            if "filtered" in request.args
            else ["in_progress", "fitting"]
        ),
    }

    conditions = []
    params: list[str] = []
    if filters["room"]:
        placeholders = ",".join("?" for _ in filters["room"])
        conditions.append(f"room IN ({placeholders})")
        params.extend(filters["room"])
    if filters["doctor"]:
        placeholders = ",".join("?" for _ in filters["doctor"])
        conditions.append(f"doctor IN ({placeholders})")
        params.extend(filters["doctor"])
    if filters["patient"]:
        placeholders = ",".join("?" for _ in filters["patient"])
        conditions.append(f"patient IN ({placeholders})")
        params.extend(filters["patient"])
    if filters["received_date_from"]:
        conditions.append("received_date >= ?")
        params.append(filters["received_date_from"])
    if filters["received_date_to"]:
        conditions.append("received_date <= ?")
        params.append(filters["received_date_to"])
    if filters["submission_date_from"] or filters["submission_date_to"]:
        conditions.append("submission_date != ''")
    if filters["submission_date_from"]:
        conditions.append("submission_date >= ?")
        params.append(filters["submission_date_from"])
    if filters["submission_date_to"]:
        conditions.append("submission_date <= ?")
        params.append(filters["submission_date_to"])
    if filters["status"]:
        status_parts = []
        if "done" in filters["status"]:
            status_parts.append("submission_date != ''")
        if "fitting" in filters["status"]:
            status_parts.append(
                "(submission_date = '' AND EXISTS ("
                "SELECT 1 FROM fittings f "
                "WHERE f.work_id = works.id AND f.returned_date IS NULL))"
            )
        if "in_progress" in filters["status"]:
            status_parts.append(
                "(submission_date = '' AND NOT EXISTS ("
                "SELECT 1 FROM fittings f "
                "WHERE f.work_id = works.id AND f.returned_date IS NULL))"
            )
        if status_parts:
            conditions.append("(" + " OR ".join(status_parts) + ")")

    sql = """
        SELECT
            id, room, doctor, patient, formula, upper_full_removable, lower_full_removable,
            work_type, note, received_date, submission_date, created_at
        FROM works
    """
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY id DESC"
    rows = db.execute(sql, params).fetchall()

    work_ids = [row["id"] for row in rows]
    fitting_rows: list[sqlite3.Row] = []
    if work_ids:
        placeholders = ",".join("?" for _ in work_ids)
        fitting_rows = db.execute(
            f"""
            SELECT id, work_id, sent_date, returned_date
            FROM fittings
            WHERE work_id IN ({placeholders})
            ORDER BY id DESC
            """,
            work_ids,
        ).fetchall()

    doctors = [
        row["doctor"]
        for row in db.execute(
            """
            SELECT DISTINCT doctor
            FROM works
            WHERE doctor != ''
            ORDER BY doctor COLLATE NOCASE
            """
        ).fetchall()
    ]
    patients = [
        row["patient"]
        for row in db.execute(
            """
            SELECT DISTINCT patient
            FROM works
            WHERE patient != ''
            ORDER BY patient COLLATE NOCASE
            """
        ).fetchall()
    ]
    rooms = [
        row["room"]
        for row in db.execute(
            """
            SELECT DISTINCT room
            FROM works
            WHERE room != ''
            ORDER BY room COLLATE NOCASE
            """
        ).fetchall()
    ]

    fittings_by_work: dict[int, list[dict[str, str | int | None]]] = {}
    open_fitting_by_work: dict[int, int] = {}
    for fitting in fitting_rows:
        work_id = fitting["work_id"]
        item = {
            "id": fitting["id"],
            "sent_date": fitting["sent_date"],
            "returned_date": fitting["returned_date"],
        }
        fittings_by_work.setdefault(work_id, []).append(item)
        if fitting["returned_date"] is None and work_id not in open_fitting_by_work:
            open_fitting_by_work[work_id] = fitting["id"]

    works = []
    for row in rows:
        selected = parse_formula(row["formula"])
        upper_full_removable = bool(row["upper_full_removable"])
        lower_full_removable = bool(row["lower_full_removable"])
        has_upper_formula = bool(selected.intersection(UPPER_TEETH)) or upper_full_removable
        has_lower_formula = bool(selected.intersection(LOWER_TEETH)) or lower_full_removable
        has_open_fitting = row["id"] in open_fitting_by_work
        if row["submission_date"]:
            status_key = "done"
            status_label = "Сдана"
        elif has_open_fitting:
            status_key = "fitting"
            status_label = "На примерке"
        else:
            status_key = "in_progress"
            status_label = "В работе"

        works.append(
            {
                "id": row["id"],
                "room": row["room"],
                "doctor": row["doctor"],
                "patient": row["patient"],
                "formula": row["formula"],
                "formula_set": selected,
                "upper_full_removable": upper_full_removable,
                "lower_full_removable": lower_full_removable,
                "work_type": row["work_type"],
                "note": row["note"] or "",
                "note_tags": parse_tags(row["note"] or ""),
                "received_date": row["received_date"],
                "submission_date": row["submission_date"],
                "created_at": row["created_at"],
                "fittings": fittings_by_work.get(row["id"], []),
                "open_fitting_id": open_fitting_by_work.get(row["id"]),
                "status_key": status_key,
                "status_label": status_label,
                "has_upper_formula": has_upper_formula,
                "has_lower_formula": has_lower_formula,
            }
        )

    return render_template(
        "list.html",
        works=works,
        formula_quadrants=FORMULA_QUADRANTS,
        today=datetime.now().date().isoformat(),
        filters=filters,
        doctors=doctors,
        patients=patients,
        rooms=rooms,
    )


@app.route("/new", methods=["GET", "POST"])
def new_work() -> str:
    if request.method == "POST":
        room = request.form.get("room", "").strip()
        doctor = request.form.get("doctor", "").strip()
        patient = request.form.get("patient", "").strip()
        selected_teeth = request.form.getlist("formula")
        upper_full_removable = bool(request.form.get("upper_full_removable"))
        lower_full_removable = bool(request.form.get("lower_full_removable"))
        work_type = request.form.get("work_type", "").strip()
        note_raw = request.form.get("note", "").strip()
        note = serialize_tags(note_raw)
        received_date = request.form.get("received_date", "").strip()

        errors = []
        if not doctor:
            errors.append("Поле 'Врач' обязательно.")
        if not patient:
            errors.append("Поле 'Пациент' обязательно.")
        if not selected_teeth and not upper_full_removable and not lower_full_removable:
            errors.append("Выберите минимум один зуб в формуле или отметьте 'Полный съемный'.")
        if not work_type:
            errors.append("Поле 'Вид работы' обязательно.")
        received_date, date_error = parse_iso_date(received_date, "Дата поступления")
        if date_error:
            errors.append(date_error)

        invalid_teeth = [item for item in selected_teeth if item not in ALL_TEETH]
        if invalid_teeth:
            errors.append("Формула содержит некорректные значения.")

        if upper_full_removable:
            selected_teeth = [item for item in selected_teeth if item not in UPPER_TEETH]
        if lower_full_removable:
            selected_teeth = [item for item in selected_teeth if item not in LOWER_TEETH]

        if errors:
            for error in errors:
                flash(error, "error")
            return render_template(
                "form.html",
                formula_quadrants=FORMULA_QUADRANTS,
                form_data=request.form,
                selected_teeth=set(selected_teeth),
                selected_flags={
                    "upper_full_removable": upper_full_removable,
                    "lower_full_removable": lower_full_removable,
                },
            )

        formula_text = ",".join(
            [
                str(tooth)
                for quadrant in FORMULA_QUADRANTS
                for tooth in quadrant["teeth"]
                if str(tooth) in selected_teeth
            ]
        )

        db = get_db()
        db.execute(
            """
            INSERT INTO works (
                room, doctor, patient, formula, upper_full_removable, lower_full_removable,
                work_type, note, received_date, submission_date, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                room,
                doctor,
                patient,
                formula_text,
                int(upper_full_removable),
                int(lower_full_removable),
                work_type,
                note,
                received_date,
                "",
                datetime.utcnow().isoformat(),
            ),
        )
        db.commit()
        flash("Работа успешно добавлена.", "success")
        return redirect(url_for("works_list"))

    return render_template(
        "form.html",
        formula_quadrants=FORMULA_QUADRANTS,
        form_data={"received_date": datetime.now().date().isoformat()},
        selected_teeth=set(),
        selected_flags={
            "upper_full_removable": False,
            "lower_full_removable": False,
        },
    )


@app.post("/works/<int:work_id>/fittings/send")
def send_to_fitting(work_id: int) -> str:
    db = get_db()
    work = db.execute("SELECT id FROM works WHERE id = ?", (work_id,)).fetchone()
    if not work:
        flash("Работа не найдена.", "error")
        return redirect(url_for("works_list"))

    open_fitting = db.execute(
        """
        SELECT id
        FROM fittings
        WHERE work_id = ? AND returned_date IS NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (work_id,),
    ).fetchone()
    if open_fitting:
        flash("Эта работа уже находится в примерке.", "error")
        return redirect(url_for("works_list"))

    sent_date, date_error = parse_iso_date(request.form.get("sent_date", ""), "Дата отправки")
    if date_error:
        flash(date_error, "error")
        return redirect(url_for("works_list"))

    db.execute(
        """
        INSERT INTO fittings (work_id, sent_date, returned_date, created_at, returned_at)
        VALUES (?, ?, NULL, ?, NULL)
        """,
        (work_id, sent_date, datetime.utcnow().isoformat()),
    )
    db.commit()
    flash("Работа отправлена в примерку.", "success")
    return redirect(url_for("works_list"))


@app.post("/works/<int:work_id>/fittings/<int:fitting_id>/return")
def return_from_fitting(work_id: int, fitting_id: int) -> str:
    db = get_db()
    fitting = db.execute(
        """
        SELECT id
        FROM fittings
        WHERE id = ? AND work_id = ? AND returned_date IS NULL
        """,
        (fitting_id, work_id),
    ).fetchone()
    if not fitting:
        flash("Открытая примерка не найдена.", "error")
        return redirect(url_for("works_list"))

    returned_date, date_error = parse_iso_date(request.form.get("returned_date", ""), "Дата возврата")
    if date_error:
        flash(date_error, "error")
        return redirect(url_for("works_list"))

    db.execute(
        """
        UPDATE fittings
        SET returned_date = ?, returned_at = ?
        WHERE id = ?
        """,
        (returned_date, datetime.utcnow().isoformat(), fitting_id),
    )
    db.commit()
    flash("Работа вернулась с примерки.", "success")
    return redirect(url_for("works_list"))


@app.post("/works/<int:work_id>/submit")
def submit_work(work_id: int) -> str:
    db = get_db()
    work = db.execute(
        """
        SELECT id, submission_date
        FROM works
        WHERE id = ?
        """,
        (work_id,),
    ).fetchone()
    if not work:
        flash("Работа не найдена.", "error")
        return redirect(url_for("works_list"))
    if work["submission_date"]:
        flash("Работа уже сдана.", "error")
        return redirect(url_for("works_list"))

    submission_date, date_error = parse_iso_date(request.form.get("submission_date", ""), "Дата сдачи")
    if date_error:
        flash(date_error, "error")
        return redirect(url_for("works_list"))

    db.execute(
        """
        UPDATE works
        SET submission_date = ?
        WHERE id = ?
        """,
        (submission_date, work_id),
    )
    db.commit()
    flash("Работа сдана.", "success")
    return redirect(url_for("works_list"))


if __name__ == "__main__":
    host = "0.0.0.0"
    port = 5005
    app.run(debug=True, host=host, port=port)

from __future__ import annotations

import functools
import os
from datetime import datetime

import psycopg2
import psycopg2.extras
from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

DATABASE_URL = os.environ["DATABASE_URL"]

import pathlib as _pathlib
_CSS_PATH = _pathlib.Path(__file__).parent / "static" / "styles.css"


def _css_ver() -> str:
    try:
        return str(int(_CSS_PATH.stat().st_mtime))
    except OSError:
        return "1"

# Формула представлена как 4 четверти зубов человека.
FORMULA_QUADRANTS = [
    {"key": "q1", "title": "Верхня права (1 чверть)", "teeth": [18, 17, 16, 15, 14, 13, 12, 11]},
    {"key": "q2", "title": "Верхня ліва (2 чверть)", "teeth": [21, 22, 23, 24, 25, 26, 27, 28]},
    {"key": "q4", "title": "Нижня права (4 чверть)", "teeth": [48, 47, 46, 45, 44, 43, 42, 41]},
    {"key": "q3", "title": "Нижня ліва (3 чверть)", "teeth": [31, 32, 33, 34, 35, 36, 37, 38]},
]
ALL_TEETH = [str(tooth) for quadrant in FORMULA_QUADRANTS for tooth in quadrant["teeth"]]
UPPER_TEETH = {str(tooth) for tooth in FORMULA_QUADRANTS[0]["teeth"] + FORMULA_QUADRANTS[1]["teeth"]}
LOWER_TEETH = {str(tooth) for tooth in FORMULA_QUADRANTS[2]["teeth"] + FORMULA_QUADRANTS[3]["teeth"]}

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-key-change-me"


@app.context_processor
def inject_css_ver():
    return {"css_ver": _css_ver()}


def get_db() -> psycopg2.extensions.connection:
    if "db" not in g:
        g.db = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return g.db


@app.teardown_appcontext
def close_db(_error: Exception | None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    cur = db.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS works (
            id SERIAL PRIMARY KEY,
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
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'works'
        """
    )
    columns = {row["column_name"] for row in cur.fetchall()}
    if "received_date" not in columns:
        cur.execute("ALTER TABLE works ADD COLUMN received_date TEXT NOT NULL DEFAULT ''")
    if "upper_full_removable" not in columns:
        cur.execute("ALTER TABLE works ADD COLUMN upper_full_removable INTEGER NOT NULL DEFAULT 0")
    if "lower_full_removable" not in columns:
        cur.execute("ALTER TABLE works ADD COLUMN lower_full_removable INTEGER NOT NULL DEFAULT 0")
    if "user_id" not in columns:
        cur.execute("ALTER TABLE works ADD COLUMN user_id INTEGER")
    if "paid_date" not in columns:
        cur.execute("ALTER TABLE works ADD COLUMN paid_date TEXT NOT NULL DEFAULT ''")
    if "paid_note" not in columns:
        cur.execute("ALTER TABLE works ADD COLUMN paid_note TEXT NOT NULL DEFAULT ''")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS fittings (
            id SERIAL PRIMARY KEY,
            work_id INTEGER NOT NULL,
            sent_date TEXT NOT NULL,
            returned_date TEXT,
            created_at TEXT NOT NULL,
            returned_at TEXT,
            FOREIGN KEY (work_id) REFERENCES works (id)
        )
        """
    )
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'fittings'
        """
    )
    fitting_columns = {row["column_name"] for row in cur.fetchall()}
    if "note" not in fitting_columns:
        cur.execute("ALTER TABLE fittings ADD COLUMN note TEXT NOT NULL DEFAULT ''")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    db.commit()
    cur.close()


@app.before_request
def ensure_db() -> None:
    init_db()


def redirect_back() -> str:
    back = (request.form.get("back", "") or session.get("last_filters", "")).strip()
    if back:
        return redirect(url_for("works_list") + "?" + back)
    return redirect(url_for("works_list"))


def login_required(view):
    @functools.wraps(view)
    def wrapped(**kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(**kwargs)
    return wrapped


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("works_list"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")

        errors = []
        if not email:
            errors.append("Email обов'язковий.")
        if not name:
            errors.append("Ім'я обов'язкове.")
        if not password:
            errors.append("Пароль обов'язковий.")
        elif len(password) < 6:
            errors.append("Пароль має бути не коротшим за 6 символів.")
        elif password != password2:
            errors.append("Паролі не збігаються.")

        if not errors:
            db = get_db()
            cur = db.cursor()
            cur.execute("SELECT id FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                errors.append("Користувач з таким email вже існує.")
            cur.close()

        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("register.html", form_data=request.form)

        db = get_db()
        cur = db.cursor()
        cur.execute(
            "INSERT INTO users (email, name, password_hash, created_at) VALUES (%s, %s, %s, %s)",
            (email, name, generate_password_hash(password), datetime.utcnow().isoformat()),
        )
        cur.close()
        db.commit()
        flash("Реєстрація пройшла успішно. Увійдіть у систему.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", form_data={})


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("works_list"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT id, name, password_hash FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        cur.close()

        if not user or not check_password_hash(user["password_hash"], password):
            flash("Невірний email або пароль.", "error")
            return render_template("login.html", form_data=request.form)

        session.clear()
        session["user_id"] = user["id"]
        session["user_name"] = user["name"]
        return redirect(url_for("works_list"))

    return render_template("login.html", form_data={})


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def get_autocomplete_data() -> dict:
    uid = session["user_id"]
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT DISTINCT doctor FROM works WHERE doctor != '' AND user_id = %s ORDER BY doctor", (uid,))
    doctors = [r["doctor"] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT patient FROM works WHERE patient != '' AND user_id = %s ORDER BY patient", (uid,))
    patients = [r["patient"] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT room FROM works WHERE room != '' AND user_id = %s ORDER BY room", (uid,))
    rooms = [r["room"] for r in cur.fetchall()]
    cur.close()
    return {"ac_doctors": doctors, "ac_patients": patients, "ac_rooms": rooms}


def parse_formula(formula_text: str) -> set[str]:
    return {item.strip() for item in formula_text.split(",") if item.strip()}



def parse_iso_date(value: str, field_title: str) -> tuple[str | None, str | None]:
    date_value = (value or "").strip()
    if not date_value:
        return None, f"Поле '{field_title}' обов'язкове."
    try:
        datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError:
        return None, f"Поле '{field_title}' має бути у форматі РРРР-ММ-ДД."
    return date_value, None


@app.route("/")
@login_required
def works_list() -> str:
    session["last_filters"] = request.query_string.decode()
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
            [s for s in request.args.getlist("status") if s in {"in_progress", "fitting", "done", "paid"}]
            if "filtered" in request.args
            else ["in_progress", "fitting"]
        ),
    }

    conditions = ["user_id = %s"]
    params: list = [session["user_id"]]
    if filters["room"]:
        placeholders = ",".join("%s" for _ in filters["room"])
        conditions.append(f"room IN ({placeholders})")
        params.extend(filters["room"])
    if filters["doctor"]:
        placeholders = ",".join("%s" for _ in filters["doctor"])
        conditions.append(f"doctor IN ({placeholders})")
        params.extend(filters["doctor"])
    if filters["patient"]:
        placeholders = ",".join("%s" for _ in filters["patient"])
        conditions.append(f"patient IN ({placeholders})")
        params.extend(filters["patient"])
    if filters["received_date_from"]:
        conditions.append("received_date >= %s")
        params.append(filters["received_date_from"])
    if filters["received_date_to"]:
        conditions.append("received_date <= %s")
        params.append(filters["received_date_to"])
    if filters["submission_date_from"] or filters["submission_date_to"]:
        conditions.append("submission_date != ''")
    if filters["submission_date_from"]:
        conditions.append("submission_date >= %s")
        params.append(filters["submission_date_from"])
    if filters["submission_date_to"]:
        conditions.append("submission_date <= %s")
        params.append(filters["submission_date_to"])
    if filters["status"]:
        status_parts = []
        if "paid" in filters["status"]:
            status_parts.append("paid_date != ''")
        if "done" in filters["status"]:
            status_parts.append("(submission_date != '' AND paid_date = '')")
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
            work_type, note, received_date, submission_date, created_at, paid_date, paid_note
        FROM works
    """
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY received_date DESC, id DESC"
    cur = db.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()

    work_ids = [row["id"] for row in rows]
    fitting_rows: list[dict] = []
    if work_ids:
        placeholders = ",".join("%s" for _ in work_ids)
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT id, work_id, sent_date, returned_date, note
            FROM fittings
            WHERE work_id IN ({placeholders})
            ORDER BY id
            """,
            work_ids,
        )
        fitting_rows = cur.fetchall()
        cur.close()

    uid = session["user_id"]
    cur = db.cursor()
    cur.execute("SELECT doctor FROM (SELECT DISTINCT doctor FROM works WHERE doctor != '' AND user_id = %s) t ORDER BY lower(doctor)", (uid,))
    doctors = [row["doctor"] for row in cur.fetchall()]
    cur.execute("SELECT patient FROM (SELECT DISTINCT patient FROM works WHERE patient != '' AND user_id = %s) t ORDER BY lower(patient)", (uid,))
    patients = [row["patient"] for row in cur.fetchall()]
    cur.execute("SELECT room FROM (SELECT DISTINCT room FROM works WHERE room != '' AND user_id = %s) t ORDER BY lower(room)", (uid,))
    rooms = [row["room"] for row in cur.fetchall()]
    cur.close()

    fittings_by_work: dict[int, list[dict[str, str | int | None]]] = {}
    open_fitting_by_work: dict[int, int] = {}
    for fitting in fitting_rows:
        work_id = fitting["work_id"]
        item = {
            "id": fitting["id"],
            "sent_date": fitting["sent_date"],
            "returned_date": fitting["returned_date"],
            "note": fitting["note"],
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
        if row["paid_date"]:
            status_key = "paid"
            status_label = "Сплачено"
        elif row["submission_date"]:
            status_key = "done"
            status_label = "Здана"
        elif has_open_fitting:
            status_key = "fitting"
            status_label = "На примірці"
        else:
            status_key = "in_progress"
            status_label = "В роботі"

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
                "received_date": row["received_date"],
                "submission_date": row["submission_date"],
                "created_at": row["created_at"],
                "paid_date": row["paid_date"],
                "paid_note": row["paid_note"],
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
@login_required
def new_work() -> str:
    if request.method == "POST":
        room = request.form.get("room", "").strip()
        doctor = request.form.get("doctor", "").strip()
        patient = request.form.get("patient", "").strip()
        selected_teeth = request.form.getlist("formula")
        upper_full_removable = bool(request.form.get("upper_full_removable"))
        lower_full_removable = bool(request.form.get("lower_full_removable"))
        work_type = request.form.get("work_type", "").strip()
        note = request.form.get("note", "").strip()
        received_date = request.form.get("received_date", "").strip()

        errors = []
        if not doctor:
            errors.append("Поле 'Лікар' обов'язкове.")
        if not work_type:
            errors.append("Поле 'Вид роботи' обов'язкове.")
        received_date, date_error = parse_iso_date(received_date, "Дата надходження")
        if date_error:
            errors.append(date_error)

        invalid_teeth = [item for item in selected_teeth if item not in ALL_TEETH]
        if invalid_teeth:
            errors.append("Формула містить некоректні значення.")

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
                **get_autocomplete_data(),
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
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO works (
                room, doctor, patient, formula, upper_full_removable, lower_full_removable,
                work_type, note, received_date, submission_date, created_at, user_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                session["user_id"],
            ),
        )
        cur.close()
        db.commit()
        flash("Роботу успішно додано.", "success")
        return redirect_back()

    back = request.args.get("back", "")
    return render_template(
        "form.html",
        formula_quadrants=FORMULA_QUADRANTS,
        form_data={"received_date": datetime.now().date().isoformat()},
        selected_teeth=set(),
        selected_flags={
            "upper_full_removable": False,
            "lower_full_removable": False,
        },
        back=back,
        **get_autocomplete_data(),
    )


@app.route("/works/<int:work_id>/edit", methods=["GET", "POST"])
@login_required
def edit_work(work_id: int) -> str:
    db = get_db()
    cur = db.cursor()
    cur.execute(
        """
        SELECT id, room, doctor, patient, formula, upper_full_removable, lower_full_removable,
               work_type, note, received_date, submission_date, paid_date, paid_note
        FROM works WHERE id = %s AND user_id = %s
        """,
        (work_id, session["user_id"]),
    )
    work = cur.fetchone()
    if not work:
        cur.close()
        flash("Роботу не знайдено.", "error")
        return redirect(url_for("works_list"))
    cur.execute(
        "SELECT id, sent_date, returned_date, note FROM fittings WHERE work_id = %s ORDER BY id",
        (work_id,),
    )
    fittings = cur.fetchall()
    cur.close()

    from urllib.parse import unquote
    back = unquote(request.args.get("back", "") or request.form.get("back", ""))

    if request.method == "POST":
        room = request.form.get("room", "").strip()
        doctor = request.form.get("doctor", "").strip()
        patient = request.form.get("patient", "").strip()
        selected_teeth = request.form.getlist("formula")
        upper_full_removable = bool(request.form.get("upper_full_removable"))
        lower_full_removable = bool(request.form.get("lower_full_removable"))
        work_type = request.form.get("work_type", "").strip()
        note = request.form.get("note", "").strip()
        received_date = request.form.get("received_date", "").strip()

        errors = []
        if not doctor:
            errors.append("Поле 'Лікар' обов'язкове.")
        if not work_type:
            errors.append("Поле 'Вид роботи' обов'язкове.")
        received_date, date_error = parse_iso_date(received_date, "Дата надходження")
        if date_error:
            errors.append(date_error)
        if [t for t in selected_teeth if t not in ALL_TEETH]:
            errors.append("Формула містить некоректні значення.")

        if upper_full_removable:
            selected_teeth = [t for t in selected_teeth if t not in UPPER_TEETH]
        if lower_full_removable:
            selected_teeth = [t for t in selected_teeth if t not in LOWER_TEETH]

        if errors:
            for error in errors:
                flash(error, "error")
            return render_template(
                "form.html",
                formula_quadrants=FORMULA_QUADRANTS,
                form_data=request.form,
                selected_teeth=set(selected_teeth),
                selected_flags={"upper_full_removable": upper_full_removable, "lower_full_removable": lower_full_removable},
                work_id=work_id,
                fittings=fittings,
                page_title="Редагувати роботу",
                submit_label="Зберегти зміни",
                **get_autocomplete_data(),
            )

        submission_date_raw = request.form.get("submission_date", "").strip()
        submission_date = submission_date_raw if submission_date_raw else ""

        formula_text = ",".join(
            str(tooth) for quadrant in FORMULA_QUADRANTS for tooth in quadrant["teeth"] if str(tooth) in selected_teeth
        )
        cur = db.cursor()
        cur.execute(
            """
            UPDATE works
            SET room = %s, doctor = %s, patient = %s, formula = %s,
                upper_full_removable = %s, lower_full_removable = %s,
                work_type = %s, note = %s, received_date = %s, submission_date = %s,
                paid_date = %s, paid_note = %s
            WHERE id = %s AND user_id = %s
            """,
            (room, doctor, patient, formula_text, int(upper_full_removable), int(lower_full_removable),
             work_type, note, received_date, submission_date,
             request.form.get("paid_date", "").strip(),
             request.form.get("paid_note", "").strip(),
             work_id, session["user_id"]),
        )
        for fitting in fittings:
            fid = fitting["id"]
            if request.form.get(f"fitting_{fid}_delete"):
                cur.execute("DELETE FROM fittings WHERE id = %s AND work_id = %s", (fid, work_id))
                continue
            sent = request.form.get(f"fitting_{fid}_sent", "").strip()
            returned = request.form.get(f"fitting_{fid}_returned", "").strip() or None
            fitting_note = request.form.get(f"fitting_{fid}_note", "").strip()
            if sent:
                cur.execute(
                    "UPDATE fittings SET sent_date = %s, returned_date = %s, note = %s WHERE id = %s",
                    (sent, returned, fitting_note, fid),
                )
        cur.close()
        db.commit()
        flash("Роботу успішно оновлено.", "success")
        if back:
            return redirect(url_for("works_list") + "?" + back)
        return redirect(url_for("works_list"))

    return render_template(
        "form.html",
        formula_quadrants=FORMULA_QUADRANTS,
        form_data={
            "room": work["room"],
            "doctor": work["doctor"],
            "patient": work["patient"],
            "work_type": work["work_type"],
            "note": work["note"] or "",
            "received_date": work["received_date"],
            "submission_date": work["submission_date"],
            "paid_date": work["paid_date"] or "",
            "paid_note": work["paid_note"] or "",
        },
        selected_teeth=parse_formula(work["formula"]),
        selected_flags={
            "upper_full_removable": bool(work["upper_full_removable"]),
            "lower_full_removable": bool(work["lower_full_removable"]),
        },
        work_id=work_id,
        fittings=fittings,
        back=back,
        page_title="Редагувати роботу",
        submit_label="Зберегти зміни",
        **get_autocomplete_data(),
    )


@app.post("/works/<int:work_id>/fittings/send")
@login_required
def send_to_fitting(work_id: int) -> str:
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id FROM works WHERE id = %s AND user_id = %s", (work_id, session["user_id"]))
    work = cur.fetchone()
    if not work:
        cur.close()
        flash("Роботу не знайдено.", "error")
        return redirect(url_for("works_list"))

    cur.execute(
        """
        SELECT id
        FROM fittings
        WHERE work_id = %s AND returned_date IS NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (work_id,),
    )
    open_fitting = cur.fetchone()
    if open_fitting:
        cur.close()
        flash("Ця робота вже знаходиться на примірці.", "error")
        return redirect(url_for("works_list"))

    sent_date, date_error = parse_iso_date(request.form.get("sent_date", ""), "Дата відправки")
    if date_error:
        cur.close()
        flash(date_error, "error")
        return redirect(url_for("works_list"))

    note = request.form.get("note", "").strip()

    cur.execute(
        """
        INSERT INTO fittings (work_id, sent_date, returned_date, created_at, returned_at, note)
        VALUES (%s, %s, NULL, %s, NULL, %s)
        """,
        (work_id, sent_date, datetime.utcnow().isoformat(), note),
    )
    cur.close()
    db.commit()
    flash("Роботу відправлено на примірку.", "success")
    return redirect_back()


@app.post("/works/<int:work_id>/fittings/<int:fitting_id>/return")
@login_required
def return_from_fitting(work_id: int, fitting_id: int) -> str:
    db = get_db()
    cur = db.cursor()
    cur.execute(
        """
        SELECT f.id
        FROM fittings f
        JOIN works w ON w.id = f.work_id
        WHERE f.id = %s AND f.work_id = %s AND f.returned_date IS NULL AND w.user_id = %s
        """,
        (fitting_id, work_id, session["user_id"]),
    )
    fitting = cur.fetchone()
    if not fitting:
        cur.close()
        flash("Відкриту примірку не знайдено.", "error")
        return redirect(url_for("works_list"))

    returned_date, date_error = parse_iso_date(request.form.get("returned_date", ""), "Дата повернення")
    if date_error:
        cur.close()
        flash(date_error, "error")
        return redirect(url_for("works_list"))

    cur.execute(
        """
        UPDATE fittings
        SET returned_date = %s, returned_at = %s
        WHERE id = %s
        """,
        (returned_date, datetime.utcnow().isoformat(), fitting_id),
    )
    cur.close()
    db.commit()
    flash("Робота повернулась з примірки.", "success")
    return redirect_back()


@app.post("/works/<int:work_id>/pay")
@login_required
def pay_work(work_id: int) -> str:
    db = get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT id, submission_date, paid_date FROM works WHERE id = %s AND user_id = %s",
        (work_id, session["user_id"]),
    )
    work = cur.fetchone()
    if not work:
        cur.close()
        flash("Роботу не знайдено.", "error")
        return redirect(url_for("works_list"))
    if not work["submission_date"]:
        cur.close()
        flash("Роботу ще не здано.", "error")
        return redirect(url_for("works_list"))
    if work["paid_date"]:
        cur.close()
        flash("Роботу вже сплачено.", "error")
        return redirect(url_for("works_list"))

    paid_date, date_error = parse_iso_date(request.form.get("paid_date", ""), "Дата оплати")
    if date_error:
        cur.close()
        flash(date_error, "error")
        return redirect(url_for("works_list"))

    paid_note = request.form.get("note", "").strip()

    cur.execute(
        "UPDATE works SET paid_date = %s, paid_note = %s WHERE id = %s",
        (paid_date, paid_note, work_id),
    )
    cur.close()
    db.commit()
    flash("Роботу відмічено як сплачену.", "success")
    return redirect_back()


@app.post("/works/<int:work_id>/submit")
@login_required
def submit_work(work_id: int) -> str:
    db = get_db()
    cur = db.cursor()
    cur.execute(
        """
        SELECT id, submission_date
        FROM works
        WHERE id = %s AND user_id = %s
        """,
        (work_id, session["user_id"]),
    )
    work = cur.fetchone()
    if not work:
        cur.close()
        flash("Роботу не знайдено.", "error")
        return redirect(url_for("works_list"))
    if work["submission_date"]:
        cur.close()
        flash("Роботу вже здано.", "error")
        return redirect(url_for("works_list"))

    submission_date, date_error = parse_iso_date(request.form.get("submission_date", ""), "Дата здачі")
    if date_error:
        cur.close()
        flash(date_error, "error")
        return redirect(url_for("works_list"))

    cur.execute(
        """
        UPDATE works
        SET submission_date = %s
        WHERE id = %s
        """,
        (submission_date, work_id),
    )
    cur.close()
    db.commit()
    flash("Роботу здано.", "success")
    return redirect_back()


if __name__ == "__main__":
    host = "0.0.0.0"
    port = 5005
    app.run(debug=True, host=host, port=port)

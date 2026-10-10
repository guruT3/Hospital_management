import os
import random
from datetime import date, datetime, timedelta

import mysql.connector
from mysql.connector import errorcode
from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, abort)

app = Flask(__name__)
app.secret_key = 'smart_hospital_secret_key_2026'

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "Guru2006@")

# Smart Digital Queue settings
QUEUE_MAX_PER_DAY = 30        # max patients per doctor per day
BOOKING_WINDOW_DAYS = 30      # how far ahead a patient may book
DEFAULT_CONSULT_MIN = 10      # used until a doctor has enough finished visits

_schema_ready = False


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def init_schema():
    """Create database/tables and run safe migrations (runs once per process)."""
    server_conn = mysql.connector.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD)
    cursor = server_conn.cursor()
    cursor.execute("CREATE DATABASE IF NOT EXISTS hospital;")
    cursor.execute("USE hospital;")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(50) UNIQUE NOT NULL,
        password VARCHAR(255) NOT NULL,
        role ENUM('admin', 'doctor', 'patient') NOT NULL
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS patients (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT UNIQUE,
        health_id VARCHAR(50) UNIQUE NOT NULL,
        name VARCHAR(100) NOT NULL,
        phone VARCHAR(20) NOT NULL,
        allergy_alerts TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS doctors (
        id INT AUTO_INCREMENT PRIMARY KEY,
        unique_id VARCHAR(50) UNIQUE NOT NULL,
        name VARCHAR(100) UNIQUE NOT NULL,
        password VARCHAR(255) NOT NULL
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS appointments (
        id INT AUTO_INCREMENT PRIMARY KEY,
        health_id VARCHAR(50) NOT NULL,
        age INT NOT NULL,
        gender VARCHAR(20) NOT NULL,
        disease TEXT NOT NULL,
        doctor_name VARCHAR(100) DEFAULT NULL,
        visit_time VARCHAR(100) DEFAULT NULL,
        status VARCHAR(20) DEFAULT 'Booked',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS feedback (
        id INT AUTO_INCREMENT PRIMARY KEY,
        appointment_id INT NOT NULL,
        health_id VARCHAR(50) NOT NULL,
        doctor_name VARCHAR(100) NOT NULL,
        rating VARCHAR(20) NOT NULL,
        feedback_text TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (appointment_id) REFERENCES appointments(id) ON DELETE CASCADE
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS medicines (
        id INT AUTO_INCREMENT PRIMARY KEY,
        appointment_id INT NOT NULL,
        medicine_name VARCHAR(150) NOT NULL,
        dosage VARCHAR(100) DEFAULT NULL,
        duration VARCHAR(100) DEFAULT NULL,
        FOREIGN KEY (appointment_id) REFERENCES appointments(id) ON DELETE CASCADE
    );
    """)

    # Safe migrations (each is ignored if the column/key already exists)
    migrations = (
        "ALTER TABLE appointments ADD COLUMN visit_done TINYINT(1) NOT NULL DEFAULT 0",
        "ALTER TABLE appointments ADD COLUMN doctor_name VARCHAR(100) DEFAULT NULL",
        "ALTER TABLE appointments ADD COLUMN visit_time VARCHAR(100) DEFAULT NULL",
        # --- Smart Digital Queue ---
        "ALTER TABLE appointments ADD COLUMN appointment_date DATE DEFAULT NULL",
        "ALTER TABLE appointments ADD COLUMN token_no INT DEFAULT NULL",
        "ALTER TABLE appointments ADD COLUMN is_emergency TINYINT(1) NOT NULL DEFAULT 0",
        "ALTER TABLE appointments ADD COLUMN queue_status VARCHAR(20) NOT NULL DEFAULT 'Waiting'",
        "ALTER TABLE appointments ADD COLUMN started_at DATETIME DEFAULT NULL",
        "ALTER TABLE appointments ADD COLUMN finished_at DATETIME DEFAULT NULL",
        "ALTER TABLE appointments ADD COLUMN skipped_at DATETIME DEFAULT NULL",
        # one token number per doctor per day (prevents duplicates)
        "ALTER TABLE appointments ADD UNIQUE KEY uq_queue_token (doctor_name, appointment_date, token_no)",
    )
    for ddl in migrations:
        try:
            cursor.execute(ddl)
        except Exception:
            pass

    # Old appointments (before the queue existed) get the date they were created
    try:
        cursor.execute("UPDATE appointments SET appointment_date = DATE(created_at) WHERE appointment_date IS NULL")
        server_conn.commit()
    except Exception:
        pass

    try:
        cursor.execute("""
        INSERT IGNORE INTO doctors (unique_id, name, password)
        VALUES ('DOC-2026-1001', 'usr123', 'master'), ('DOC-2026-1002', 'demo doctor', 'master');
        """)
        server_conn.commit()
    except Exception:
        pass

    cursor.close()
    server_conn.close()


def get_db():
    global _schema_ready
    if not _schema_ready:
        init_schema()
        _schema_ready = True

    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database="hospital"
    )


def generate_health_id():
    return f"MD-2026-{random.randint(1000, 9999)}"


def generate_doctor_id():
    return f"DOC-2026-{random.randint(1000, 9999)}"


# ---------------------------------------------------------------------------
# Smart Digital Queue helpers
# ---------------------------------------------------------------------------
def parse_date(value, default=None):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return default


def avg_consult_minutes(cursor, doctor_name):
    """Average real consultation length for a doctor (last 30 days)."""
    cursor.execute("""
        SELECT AVG(TIMESTAMPDIFF(SECOND, started_at, finished_at)) AS secs, COUNT(*) AS n
        FROM appointments
        WHERE doctor_name = %s
          AND started_at IS NOT NULL AND finished_at IS NOT NULL
          AND finished_at > started_at
          AND finished_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
    """, (doctor_name,))
    row = cursor.fetchone() or {}
    if row.get('n') and row['n'] >= 3 and row.get('secs'):
        return int(round(max(3, min(45, float(row['secs']) / 60.0))))
    return DEFAULT_CONSULT_MIN


def compute_queue(cursor, doctor_name, qdate):
    """
    Build the queue for one doctor on one date.
    Order: In Consultation -> Waiting (emergency first, then token) -> Skipped (back of line) -> Done.
    """
    cursor.execute("""
        SELECT * FROM appointments
        WHERE doctor_name = %s AND appointment_date = %s
          AND token_no IS NOT NULL AND status <> 'Cancelled'
    """, (doctor_name, qdate))
    rows = cursor.fetchall()

    current = [r for r in rows if r['queue_status'] == 'In Consultation']
    waiting = sorted([r for r in rows if r['queue_status'] == 'Waiting'],
                     key=lambda r: (0 if r['is_emergency'] else 1, r['token_no']))
    skipped = sorted([r for r in rows if r['queue_status'] == 'Skipped'],
                     key=lambda r: (r['skipped_at'] or datetime.min, r['token_no']))
    done = sorted([r for r in rows if r['queue_status'] == 'Done'], key=lambda r: r['token_no'])

    avg_min = avg_consult_minutes(cursor, doctor_name)
    order = current + waiting + skipped

    for idx, r in enumerate(order):
        r['ahead'] = idx
        r['position'] = idx + 1
        r['wait_min'] = 0 if r['queue_status'] == 'In Consultation' else int(round(idx * avg_min))

    return {
        'rows': order + done,
        'order': order,
        'now_serving': current[0]['token_no'] if current else None,
        'waiting_count': len(waiting) + len(skipped),
        'done_count': len(done),
        'total': len(rows),
        'avg_min': avg_min,
    }


def _go_dashboard():
    d = (request.form.get("date") or "").strip()
    if parse_date(d):
        return redirect(url_for('doctor_dashboard', date=d))
    return redirect(url_for('doctor_dashboard'))


def _set_doc_msg(msg, kind='success'):
    session['doc_msg'] = msg
    session['doc_msg_type'] = kind


def _owned_appointment(cursor, appointment_id):
    cursor.execute("SELECT * FROM appointments WHERE id = %s", (appointment_id,))
    appt = cursor.fetchone()
    if not appt or appt['doctor_name'] != session.get('username'):
        return None
    return appt


# ---------------------------------------------------------------------------
# Public pages / auth
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        login_input = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        role = (request.form.get("role") or "patient").strip()

        if not login_input or not password:
            error = "Please enter both Username/Phone/Health ID and Password."
        else:
            try:
                db = get_db()
                cursor = db.cursor(dictionary=True)

                cursor.execute("SELECT * FROM doctors WHERE (name = %s OR unique_id = %s) AND password = %s", (login_input, login_input, password))
                doc = cursor.fetchone()

                if doc and (role == 'doctor' or login_input == 'usr123' or login_input == 'demo doctor'):
                    session['user_id'] = doc['id']
                    session['username'] = doc['name']
                    session['doctor_unique_id'] = doc.get('unique_id') or f"DOC-2026-{doc['id']}"
                    session['role'] = 'doctor'
                    cursor.close()
                    db.close()
                    return redirect(url_for('doctor_dashboard'))

                query = """
                SELECT u.*, p.health_id, p.name AS patient_name
                FROM users u
                LEFT JOIN patients p ON u.id = p.user_id
                WHERE (u.username = %s OR p.phone = %s OR p.health_id = %s)
                  AND u.password = %s
                """
                cursor.execute(query, (login_input, login_input, login_input, password))
                matched_users = cursor.fetchall()

                if matched_users:
                    user = next((u for u in matched_users if u['role'] == role), None)
                    if not user:
                        user = matched_users[0]

                    if user:
                        session['user_id'] = user['id']
                        session['username'] = user['username']
                        session['role'] = user['role']

                        if user['role'] == 'patient':
                            session['health_id'] = user.get('health_id') or 'MD-2026-ACTIVE'
                            session['name'] = user.get('patient_name') or user['username']

                        cursor.close()
                        db.close()

                        if user['role'] == 'admin':
                            return redirect(url_for('admin_dashboard'))
                        elif user['role'] == 'doctor':
                            return redirect(url_for('doctor_dashboard'))
                        else:
                            return redirect(url_for('patient_dashboard'))
                else:
                    error = "Invalid Username/Phone/Health ID or Password."
                    cursor.close()
                    db.close()
            except Exception as e:
                error = f"Database Login Error: {e}"

    return render_template("login.html", error=error)


@app.route("/doctor/login", methods=["GET", "POST"])
def doctor_login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        try:
            db = get_db()
            cursor = db.cursor(dictionary=True)
            cursor.execute("SELECT * FROM doctors WHERE (name = %s OR unique_id = %s) AND password = %s", (username, username, password))
            doc = cursor.fetchone()
            cursor.close()
            db.close()

            if doc:
                session['user_id'] = doc['id']
                session['username'] = doc['name']
                session['doctor_unique_id'] = doc.get('unique_id') or f"DOC-2026-{doc['id']}"
                session['role'] = "doctor"
                return redirect(url_for('doctor_dashboard'))
            elif username == "usr123" and password == "master":
                session['user_id'] = 9999
                session['username'] = "usr123"
                session['doctor_unique_id'] = "DOC-2026-1001"
                session['role'] = "doctor"
                return redirect(url_for('doctor_dashboard'))
            else:
                return render_template("doctor_login.html", error="Invalid Doctor Name/ID or Password.", default_role="doctor")
        except Exception as e:
            return render_template("login.html", error=f"Database Error: {e}", default_role="doctor")

    return render_template("doctor_login.html", default_role="doctor")


@app.route("/register", methods=["GET", "POST"])
def register():
    generated_id = None
    error = None
    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        allergies = (request.form.get("allergies") or "").strip()

        health_id = generate_health_id()

        try:
            db = get_db()
            cursor = db.cursor()

            cursor.execute("INSERT INTO users (username, password, role) VALUES (%s, %s, 'patient')", (username, password))
            user_id = cursor.lastrowid

            try:
                cursor.execute(
                    "INSERT INTO patients (user_id, health_id, name, phone, allergy_alerts) VALUES (%s, %s, %s, %s, %s)",
                    (user_id, health_id, full_name, phone, allergies)
                )
            except Exception:
                cursor.execute(
                    "INSERT INTO patients (user_id, health_id, name, phone) VALUES (%s, %s, %s, %s)",
                    (user_id, health_id, full_name, phone)
                )

            db.commit()
            cursor.close()
            db.close()

            session['user_id'] = user_id
            session['username'] = username
            session['role'] = 'patient'
            session['health_id'] = health_id
            session['name'] = full_name

            generated_id = health_id
        except Exception as e:
            error = f"Registration Error: {e}"

    return render_template("register.html", generated_id=generated_id, error=error)


# ---------------------------------------------------------------------------
# Booking (with token assignment)
# ---------------------------------------------------------------------------
@app.route("/appointments", methods=["GET", "POST"])
def appointments():
    success_message = None
    error_message = None
    ticket = None
    health_id = session.get('health_id', '')
    today = date.today()
    max_date = today + timedelta(days=BOOKING_WINDOW_DAYS)
    doctors_list = []
    form_date = today.isoformat()
    form_doctor = ""

    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM doctors ORDER BY name ASC")
        doctors_list = cursor.fetchall()
        cursor.close()
        db.close()
    except Exception as e:
        print(f"Error fetching doctors: {e}")

    if request.method == "POST":
        health_id = request.form.get("health_id", "").strip()
        age = request.form.get("age", "").strip()
        gender = request.form.get("gender", "").strip()
        disease = request.form.get("disease", "").strip()
        doctor_name = request.form.get("doctor_name", "").strip()
        date_str = request.form.get("appointment_date", "").strip()
        is_emergency = 1 if request.form.get("is_emergency") == "on" else 0
        form_date = date_str or form_date
        form_doctor = doctor_name

        appt_date = parse_date(date_str)
        doctor_names = {d['name'] for d in doctors_list}

        if not (health_id and age and gender and disease and doctor_name and date_str):
            error_message = "Please fill in Health ID, Age, Gender, Doctor, Date and Disease/Symptoms."
        elif not age.isdigit() or not (1 <= int(age) <= 120):
            error_message = "Please enter a valid age between 1 and 120."
        elif appt_date is None or appt_date < today or appt_date > max_date:
            error_message = f"Please choose a date between today and {max_date.strftime('%d %b %Y')}."
        elif doctor_name not in doctor_names:
            error_message = "Please select a valid doctor from the list."
        else:
            db = None
            try:
                db = get_db()
                cursor = db.cursor(dictionary=True)

                cursor.execute("""
                    SELECT COUNT(*) AS c FROM appointments
                    WHERE doctor_name = %s AND appointment_date = %s
                      AND token_no IS NOT NULL AND status <> 'Cancelled'
                """, (doctor_name, appt_date))
                booked = cursor.fetchone()['c']

                cursor.execute("""
                    SELECT id FROM appointments
                    WHERE health_id = %s AND doctor_name = %s AND appointment_date = %s
                      AND status <> 'Cancelled'
                """, (health_id, doctor_name, appt_date))
                duplicate = cursor.fetchone()

                if booked >= QUEUE_MAX_PER_DAY:
                    error_message = (f"{doctor_name} is fully booked on {appt_date.strftime('%d %b %Y')} "
                                     f"({QUEUE_MAX_PER_DAY} patients). Please choose another date or doctor.")
                elif duplicate:
                    error_message = (f"You already have appointment #{duplicate['id']} with {doctor_name} "
                                     f"on {appt_date.strftime('%d %b %Y')}.")
                else:
                    token = None
                    new_id = None
                    for _ in range(5):
                        cursor.execute("""
                            SELECT COALESCE(MAX(token_no), 0) + 1 AS t FROM appointments
                            WHERE doctor_name = %s AND appointment_date = %s
                        """, (doctor_name, appt_date))
                        nxt = int(cursor.fetchone()['t'])
                        try:
                            cursor.execute("""
                                INSERT INTO appointments
                                (health_id, age, gender, disease, doctor_name,
                                 appointment_date, token_no, is_emergency, queue_status)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Waiting')
                            """, (health_id, int(age), gender, disease, doctor_name,
                                  appt_date, nxt, is_emergency))
                            db.commit()
                            token, new_id = nxt, cursor.lastrowid
                            break
                        except mysql.connector.IntegrityError as e:
                            db.rollback()
                            if e.errno != errorcode.ER_DUP_ENTRY:
                                raise

                    if token is None:
                        error_message = "The queue is busy right now. Please try booking again."
                    else:
                        ticket = {
                            'token': token,
                            'doctor': doctor_name,
                            'date_label': appt_date.strftime('%d %b %Y'),
                            'is_today': appt_date == today,
                            'emergency': bool(is_emergency),
                            'ahead': None,
                            'wait_min': None,
                        }
                        if appt_date == today:
                            q = compute_queue(cursor, doctor_name, appt_date)
                            me = next((r for r in q['rows'] if r['id'] == new_id), None)
                            if me and 'ahead' in me:
                                ticket['ahead'] = me['ahead']
                                ticket['wait_min'] = me['wait_min']
                        success_message = f"Appointment booked for Health ID: {health_id}."
            except Exception as e:
                error_message = f"Appointment Booking Error: {e}"
            finally:
                if db:
                    try:
                        db.close()
                    except Exception:
                        pass

    return render_template(
        "appointments.html",
        health_id=health_id, doctors=doctors_list,
        success_message=success_message, error_message=error_message, ticket=ticket,
        today_str=today.isoformat(), max_str=max_date.isoformat(),
        form_date=form_date, form_doctor=form_doctor,
        max_per_day=QUEUE_MAX_PER_DAY
    )


@app.route("/appointment/cancel/<int:appointment_id>", methods=["POST"])
def patient_cancel_appointment(appointment_id):
    if session.get('role') != 'patient':
        return redirect(url_for('patient_login'))
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("""
            UPDATE appointments SET status = 'Cancelled'
            WHERE id = %s AND health_id = %s AND status = 'Booked'
              AND queue_status IN ('Waiting', 'Skipped')
        """, (appointment_id, session.get('health_id')))
        db.commit()
        cursor.close()
        db.close()
    except Exception as e:
        print(f"Error cancelling appointment: {e}")
    return redirect(url_for('patient_dashboard'))


# ---------------------------------------------------------------------------
# Doctor: queue actions
# ---------------------------------------------------------------------------
@app.route("/doctor/queue/call-next", methods=["POST"])
def queue_call_next():
    if session.get('role') != 'doctor':
        return redirect(url_for('login'))

    doctor_name = session.get('username')
    today = date.today()
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        q = compute_queue(cursor, doctor_name, today)

        # Finish whoever is currently inside
        cursor.execute("""
            UPDATE appointments SET queue_status = 'Done', finished_at = NOW()
            WHERE doctor_name = %s AND appointment_date = %s AND queue_status = 'In Consultation'
        """, (doctor_name, today))

        nxt = next((r for r in q['order'] if r['queue_status'] in ('Waiting', 'Skipped')), None)
        if nxt:
            cursor.execute("""
                UPDATE appointments
                SET queue_status = 'In Consultation', started_at = NOW(),
                    finished_at = NULL, skipped_at = NULL
                WHERE id = %s
            """, (nxt['id'],))
            _set_doc_msg(f"Now serving token #{nxt['token_no']} ({nxt['health_id']}).")
        else:
            _set_doc_msg("No more patients waiting in today's queue.", 'info')

        db.commit()
        cursor.close()
        db.close()
    except Exception as e:
        _set_doc_msg(f"Could not call next patient: {e}", 'error')

    return redirect(url_for('doctor_dashboard'))


@app.route("/doctor/queue/<int:appointment_id>/skip", methods=["POST"])
def queue_skip(appointment_id):
    if session.get('role') != 'doctor':
        return redirect(url_for('login'))
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        appt = _owned_appointment(cursor, appointment_id)
        if not appt:
            _set_doc_msg("Appointment not found in your queue.", 'error')
        elif appt['appointment_date'] != date.today():
            _set_doc_msg("You can only skip patients in today's queue.", 'error')
        elif appt['queue_status'] not in ('Waiting', 'In Consultation'):
            _set_doc_msg("This patient can't be skipped.", 'error')
        else:
            cursor.execute("""
                UPDATE appointments
                SET queue_status = 'Skipped', skipped_at = NOW(), started_at = NULL, finished_at = NULL
                WHERE id = %s
            """, (appointment_id,))
            db.commit()
            _set_doc_msg(f"Token #{appt['token_no']} moved to the back of the queue.")
        cursor.close()
        db.close()
    except Exception as e:
        _set_doc_msg(f"Error: {e}", 'error')
    return _go_dashboard()


@app.route("/doctor/queue/<int:appointment_id>/done", methods=["POST"])
def queue_done(appointment_id):
    if session.get('role') != 'doctor':
        return redirect(url_for('login'))
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        appt = _owned_appointment(cursor, appointment_id)
        if not appt:
            _set_doc_msg("Appointment not found in your queue.", 'error')
        elif appt['appointment_date'] != date.today() or appt['queue_status'] != 'In Consultation':
            _set_doc_msg("Only the patient currently in consultation can be marked done.", 'error')
        else:
            cursor.execute("UPDATE appointments SET queue_status = 'Done', finished_at = NOW() WHERE id = %s",
                           (appointment_id,))
            db.commit()
            _set_doc_msg(f"Token #{appt['token_no']} marked done.")
        cursor.close()
        db.close()
    except Exception as e:
        _set_doc_msg(f"Error: {e}", 'error')
    return _go_dashboard()


@app.route("/doctor/queue/<int:appointment_id>/emergency", methods=["POST"])
def queue_emergency(appointment_id):
    if session.get('role') != 'doctor':
        return redirect(url_for('login'))
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        appt = _owned_appointment(cursor, appointment_id)
        if not appt:
            _set_doc_msg("Appointment not found in your queue.", 'error')
        elif appt['queue_status'] not in ('Waiting', 'Skipped'):
            _set_doc_msg("Emergency can only be changed for patients who are still waiting.", 'error')
        else:
            new_val = 0 if appt['is_emergency'] else 1
            cursor.execute("UPDATE appointments SET is_emergency = %s WHERE id = %s", (new_val, appointment_id))
            db.commit()
            _set_doc_msg(f"Token #{appt['token_no']} {'marked as EMERGENCY' if new_val else 'set back to routine'}.")
        cursor.close()
        db.close()
    except Exception as e:
        _set_doc_msg(f"Error: {e}", 'error')
    return _go_dashboard()


@app.route("/doctor/queue/panel")
def doctor_queue_panel():
    """HTML fragment polled by the doctor dashboard every few seconds."""
    if session.get('role') != 'doctor':
        abort(401)
    today = date.today()
    qdate = parse_date(request.args.get('date'), today)
    db = get_db()
    cursor = db.cursor(dictionary=True)
    q = compute_queue(cursor, session.get('username'), qdate)
    cursor.close()
    db.close()
    return render_template("_doctor_queue_panel.html", q=q, qdate=qdate, today=today, is_today=(qdate == today))


# ---------------------------------------------------------------------------
# Patient: live queue API
# ---------------------------------------------------------------------------
@app.route("/api/queue/mine")
def api_queue_mine():
    if session.get('role') != 'patient' or not session.get('health_id'):
        return jsonify({"items": []}), 401

    today = date.today()
    items = []
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT * FROM appointments
            WHERE health_id = %s AND status <> 'Cancelled' AND token_no IS NOT NULL
              AND queue_status IN ('Waiting', 'In Consultation', 'Skipped')
              AND appointment_date >= %s
            ORDER BY appointment_date ASC, token_no ASC
        """, (session.get('health_id'), today))
        mine = cursor.fetchall()

        cache = {}
        for a in mine:
            item = {
                "id": a['id'],
                "token": a['token_no'],
                "doctor": a['doctor_name'],
                "date": a['appointment_date'].isoformat(),
                "date_label": a['appointment_date'].strftime('%d %b %Y'),
                "is_today": a['appointment_date'] == today,
                "queue_status": a['queue_status'],
                "emergency": bool(a['is_emergency']),
                "ahead": None, "wait_min": None, "now_serving": None, "avg_min": None,
            }
            if item["is_today"]:
                key = (a['doctor_name'], a['appointment_date'])
                if key not in cache:
                    cache[key] = compute_queue(cursor, a['doctor_name'], a['appointment_date'])
                q = cache[key]
                me = next((r for r in q['order'] if r['id'] == a['id']), None)
                if me:
                    item["ahead"] = me['ahead']
                    item["wait_min"] = me['wait_min']
                item["now_serving"] = q['now_serving']
                item["avg_min"] = q['avg_min']
            items.append(item)

        cursor.close()
        db.close()
    except Exception as e:
        print(f"Queue API error: {e}")
        return jsonify({"items": []}), 500

    return jsonify({"items": items})


# ---------------------------------------------------------------------------
# Doctor: confirm + prescribe
# ---------------------------------------------------------------------------
@app.route("/doctor/confirm-appointment", methods=["POST"])
def confirm_appointment():
    if session.get('role') != 'doctor':
        return redirect(url_for('login'))

    doctor_name = session.get('username')
    appointment_id = (request.form.get("appointment_id") or "").strip()
    visit_time = (request.form.get("visit_time") or "").strip()

    medicine_names = request.form.getlist("medicine_name")
    dosages = request.form.getlist("dosage")
    durations = request.form.getlist("duration")

    if appointment_id.isdigit() and visit_time:
        try:
            db = get_db()
            cursor = db.cursor(dictionary=True)
            cursor.execute("SELECT * FROM appointments WHERE id = %s", (int(appointment_id),))
            appt = cursor.fetchone()

            if not appt or (appt['doctor_name'] and appt['doctor_name'] != doctor_name):
                _set_doc_msg("You can only confirm your own appointments.", 'error')
            elif appt['status'] == 'Cancelled':
                _set_doc_msg("This appointment was cancelled.", 'error')
            elif appt['appointment_date'] and appt['appointment_date'] > date.today():
                _set_doc_msg("This appointment is for a future date. It opens on the day of the visit.", 'error')
            else:
                cursor.execute("""
                    UPDATE appointments
                    SET doctor_name = %s, visit_time = %s, status = 'Confirmed',
                        queue_status = 'Done', finished_at = COALESCE(finished_at, NOW())
                    WHERE id = %s
                """, (doctor_name, visit_time, appt['id']))

                cursor.execute("DELETE FROM medicines WHERE appointment_id = %s", (appt['id'],))
                for i in range(len(medicine_names)):
                    name = (medicine_names[i] or "").strip()
                    if not name:
                        continue
                    dosage = (dosages[i] if i < len(dosages) else "").strip()
                    duration = (durations[i] if i < len(durations) else "").strip()
                    cursor.execute(
                        "INSERT INTO medicines (appointment_id, medicine_name, dosage, duration) VALUES (%s, %s, %s, %s)",
                        (appt['id'], name, dosage, duration)
                    )
                db.commit()
                _set_doc_msg(f"Appointment #{appt['id']} confirmed and prescription saved.")

            cursor.close()
            db.close()
        except Exception as e:
            print(f"Error confirming appointment: {e}")
            _set_doc_msg(f"Error confirming appointment: {e}", 'error')

    return redirect(url_for('doctor_dashboard', date=request.form.get("date")) if parse_date(request.form.get("date")) else url_for('doctor_dashboard'))


@app.route("/prescription/download/<int:appointment_id>")
def download_prescription(appointment_id):
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM appointments WHERE id = %s", (appointment_id,))
        appointment = cursor.fetchone()

        patient_info = None
        medicines_list = []
        if appointment:
            cursor.execute("SELECT * FROM patients WHERE health_id = %s", (appointment['health_id'],))
            patient_info = cursor.fetchone()

            cursor.execute("SELECT * FROM medicines WHERE appointment_id = %s ORDER BY id ASC", (appointment_id,))
            medicines_list = cursor.fetchall()

        cursor.close()
        db.close()

        if not appointment:
            return redirect(url_for('patient_dashboard'))

        return render_template("prescription_pdf.html", appt=appointment, patient=patient_info, medicines=medicines_list)
    except Exception as e:
        print(f"Error downloading prescription: {e}")
        return redirect(url_for('patient_dashboard'))


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
@app.route("/admin/add-doctor", methods=["POST"])
def add_doctor():
    name = (request.form.get("name") or "").strip()
    password = (request.form.get("password") or "master").strip()
    custom_unique_id = (request.form.get("unique_id") or "").strip()

    unique_id = custom_unique_id if custom_unique_id else generate_doctor_id()

    if name and password:
        try:
            db = get_db()
            cursor = db.cursor()
            cursor.execute(
                "INSERT INTO doctors (unique_id, name, password) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE password = %s",
                (unique_id, name, password, password)
            )
            db.commit()
            cursor.close()
            db.close()
        except Exception as e:
            print(f"Error adding doctor: {e}")

    return redirect(url_for('admin_dashboard'))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('index'))


@app.route("/patient/dashboard")
def patient_dashboard():
    if not session.get('user_id'):
        return redirect(url_for('login'))

    appointments_list = []
    medicines_map = {}
    health_id = session.get('health_id')
    if health_id:
        try:
            db = get_db()
            cursor = db.cursor(dictionary=True)
            cursor.execute("SELECT * FROM appointments WHERE health_id = %s ORDER BY id DESC", (health_id,))
            appointments_list = cursor.fetchall()

            if appointments_list:
                appt_ids = [a['id'] for a in appointments_list]
                placeholders = ','.join(['%s'] * len(appt_ids))
                cursor.execute(
                    f"SELECT * FROM medicines WHERE appointment_id IN ({placeholders}) ORDER BY id ASC",
                    tuple(appt_ids)
                )
                for row in cursor.fetchall():
                    medicines_map.setdefault(row['appointment_id'], []).append(row)

            cursor.close()
            db.close()
        except Exception as e:
            print(f"Error fetching appointments: {e}")

    return render_template("patient_dashboard.html", appointments=appointments_list,
                           medicines_map=medicines_map, today=date.today())


@app.route("/doctor/dashboard")
def doctor_dashboard():
    if session.get('role') != 'doctor':
        return redirect(url_for('login'))

    doctor_name = session.get('username')
    today = date.today()
    qdate = parse_date(request.args.get('date'), today)

    consult_list = []
    doctors_list = []
    medicines_map = {}
    q = {'rows': [], 'order': [], 'now_serving': None, 'waiting_count': 0,
         'done_count': 0, 'total': 0, 'avg_min': DEFAULT_CONSULT_MIN}
    confirmed_total = 0

    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)

        q = compute_queue(cursor, doctor_name, qdate)

        # Older bookings made before the queue existed (no token)
        cursor.execute("""
            SELECT * FROM appointments
            WHERE token_no IS NULL AND (doctor_name IS NULL OR doctor_name = %s)
              AND status <> 'Cancelled'
            ORDER BY id DESC
        """, (doctor_name,))
        legacy = cursor.fetchall()
        consult_list = q['rows'] + legacy

        cursor.execute("SELECT COUNT(*) AS c FROM appointments WHERE doctor_name = %s AND status = 'Confirmed'",
                       (doctor_name,))
        confirmed_total = cursor.fetchone()['c']

        cursor.execute("SELECT * FROM doctors ORDER BY name ASC")
        doctors_list = cursor.fetchall()

        if consult_list:
            ids = [a['id'] for a in consult_list]
            placeholders = ','.join(['%s'] * len(ids))
            cursor.execute(f"SELECT * FROM medicines WHERE appointment_id IN ({placeholders}) ORDER BY id ASC", tuple(ids))
            for row in cursor.fetchall():
                medicines_map.setdefault(row['appointment_id'], []).append(row)

        cursor.close()
        db.close()
    except Exception as e:
        print(f"Error fetching doctor data: {e}")

    doc_msg = session.pop('doc_msg', None)
    doc_msg_type = session.pop('doc_msg_type', None)

    return render_template(
        "doctor_dashboard.html",
        appointments=consult_list, doctors=doctors_list, medicines_map=medicines_map,
        q=q, qdate=qdate, today=today, is_today=(qdate == today),
        tomorrow=today + timedelta(days=1),
        max_date=today + timedelta(days=BOOKING_WINDOW_DAYS),
        confirmed_total=confirmed_total,
        doc_msg=doc_msg, doc_msg_type=doc_msg_type
    )


@app.route("/patient/login", methods=["GET", "POST"])
def patient_login():
    error = None
    if request.method == "POST":
        login_input = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        if not login_input or not password:
            error = "Please enter both Username/Phone/Health ID and Password."
        else:
            try:
                db = get_db()
                cursor = db.cursor(dictionary=True)
                query = """
                SELECT u.*, p.health_id, p.name AS patient_name
                FROM users u
                LEFT JOIN patients p ON u.id = p.user_id
                WHERE (u.username = %s OR p.phone = %s OR p.health_id = %s)
                  AND u.password = %s AND u.role = 'patient'
                """
                cursor.execute(query, (login_input, login_input, login_input, password))
                user = cursor.fetchone()
                cursor.close()
                db.close()

                if user:
                    session['user_id'] = user['id']
                    session['username'] = user['username']
                    session['role'] = 'patient'
                    session['health_id'] = user.get('health_id') or 'MD-2026-ACTIVE'
                    session['name'] = user.get('patient_name') or user['username']
                    return redirect(url_for('patient_dashboard'))
                else:
                    error = "Invalid Username/Phone/Health ID or Password."
            except Exception as e:
                error = f"Database Login Error: {e}"

    return render_template("patient_login.html", error=error)


@app.route("/admin/dashboard")
def admin_dashboard():
    if session.get('role') != 'admin':
        return redirect(url_for('admin_login'))

    doctors_list = []
    patients_list = []
    appointments_list = []
    feedback_map = {}

    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)

        cursor.execute("""
            SELECT d.*,
                   (SELECT COUNT(*) FROM appointments a
                    WHERE a.doctor_name = d.name AND a.status = 'Confirmed') AS visit_count
            FROM doctors d
            ORDER BY d.id DESC
        """)
        doctors_list = cursor.fetchall()

        cursor.execute("""
            SELECT u.id AS user_id, u.username, p.health_id, p.name, p.phone, p.allergy_alerts
            FROM patients p
            JOIN users u ON p.user_id = u.id
            ORDER BY p.id DESC
        """)
        patients_list = cursor.fetchall()

        cursor.execute("SELECT * FROM appointments ORDER BY id DESC")
        appointments_list = cursor.fetchall()

        cursor.execute("""
            SELECT doctor_name,
                   SUM(CASE WHEN rating = 'Good' THEN 1 ELSE 0 END) AS good_count,
                   SUM(CASE WHEN rating = 'Average' THEN 1 ELSE 0 END) AS average_count,
                   SUM(CASE WHEN rating = 'Poor' THEN 1 ELSE 0 END) AS poor_count,
                   COUNT(*) AS total_feedback
            FROM feedback
            GROUP BY doctor_name
        """)
        feedback_rows = cursor.fetchall()
        feedback_map = {row['doctor_name']: row for row in feedback_rows}

        cursor.close()
        db.close()
    except Exception as e:
        print(f"Error fetching admin data: {e}")

    admin_msg = session.pop('admin_msg', None)
    admin_msg_type = session.pop('admin_msg_type', None)

    return render_template(
        "admin_dashboard.html",
        doctors=doctors_list,
        patients=patients_list,
        appointments=appointments_list,
        feedback_map=feedback_map,
        admin_msg=admin_msg,
        admin_msg_type=admin_msg_type
    )


@app.route("/admin/cancel-appointment/<int:appointment_id>", methods=["POST"])
def admin_cancel_appointment(appointment_id):
    if session.get('role') != 'admin':
        return redirect(url_for('admin_login'))

    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("UPDATE appointments SET status = 'Cancelled' WHERE id = %s", (appointment_id,))
        db.commit()
        cursor.close()
        db.close()
        session['admin_msg'] = f"Appointment #{appointment_id} cancelled."
        session['admin_msg_type'] = 'success'
    except Exception as e:
        session['admin_msg'] = f"Error cancelling appointment: {e}"
        session['admin_msg_type'] = 'error'

    return redirect(url_for('admin_dashboard'))


@app.route("/admin/change-password", methods=["POST"])
def change_admin_password():
    if session.get('role') != 'admin':
        return redirect(url_for('admin_login'))

    current_password = (request.form.get("current_password") or "").strip()
    new_password = (request.form.get("new_password") or "").strip()
    confirm_password = (request.form.get("confirm_password") or "").strip()

    if not current_password or not new_password or not confirm_password:
        session['admin_msg'] = "Please fill in all password fields."
        session['admin_msg_type'] = 'error'
        return redirect(url_for('admin_dashboard'))

    if new_password != confirm_password:
        session['admin_msg'] = "New password and confirmation do not match."
        session['admin_msg_type'] = 'error'
        return redirect(url_for('admin_dashboard'))

    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE id = %s AND role = 'admin'", (session.get('user_id'),))
        admin = cursor.fetchone()

        if not admin or admin['password'] != current_password:
            session['admin_msg'] = "Current password is incorrect."
            session['admin_msg_type'] = 'error'
        else:
            cursor.execute("UPDATE users SET password = %s WHERE id = %s", (new_password, session.get('user_id')))
            db.commit()
            session['admin_msg'] = "Password updated successfully."
            session['admin_msg_type'] = 'success'

        cursor.close()
        db.close()
    except Exception as e:
        session['admin_msg'] = f"Error updating password: {e}"
        session['admin_msg_type'] = 'error'

    return redirect(url_for('admin_dashboard'))


@app.route("/admin/delete-patient/<int:user_id>", methods=["POST"])
def delete_patient(user_id):
    if session.get('role') != 'admin':
        return redirect(url_for('admin_login'))

    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("DELETE FROM users WHERE id = %s AND role = 'patient'", (user_id,))
        db.commit()
        cursor.close()
        db.close()
    except Exception as e:
        print(f"Error deleting patient: {e}")

    return redirect(url_for('admin_dashboard'))


@app.route("/admin/delete-doctor/<int:doctor_id>", methods=["POST"])
def delete_doctor(doctor_id):
    if session.get('role') != 'admin':
        return redirect(url_for('admin_login'))

    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("DELETE FROM doctors WHERE id = %s", (doctor_id,))
        db.commit()
        cursor.close()
        db.close()
    except Exception as e:
        print(f"Error deleting doctor: {e}")

    return redirect(url_for('admin_dashboard'))


@app.route("/feedback/<int:appointment_id>", methods=["GET"])
def feedback_form(appointment_id):
    if session.get('role') != 'patient':
        return redirect(url_for('patient_login'))

    appt = None
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM appointments WHERE id = %s AND health_id = %s",
            (appointment_id, session.get('health_id'))
        )
        appt = cursor.fetchone()
        cursor.close()
        db.close()
    except Exception as e:
        print(f"Error loading feedback form: {e}")

    if not appt or appt['status'] != 'Confirmed' or appt.get('visit_done'):
        return redirect(url_for('patient_dashboard'))

    return render_template("feedback.html", appt=appt)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        if not username or not password:
            error = "Please enter both Username and Password."
        else:
            try:
                db = get_db()
                cursor = db.cursor(dictionary=True)
                cursor.execute(
                    "SELECT * FROM users WHERE username = %s AND password = %s AND role = 'admin'",
                    (username, password)
                )
                admin = cursor.fetchone()
                cursor.close()
                db.close()

                if admin:
                    session['user_id'] = admin['id']
                    session['username'] = admin['username']
                    session['role'] = 'admin'
                    return redirect(url_for('admin_dashboard'))
                else:
                    error = "Invalid Admin Username or Password."
            except Exception as e:
                error = f"Database Login Error: {e}"

    return render_template("admin_login.html", error=error)


@app.route("/feedback/submit", methods=["POST"])
def submit_feedback():
    if session.get('role') != 'patient':
        return redirect(url_for('patient_login'))

    appointment_id = request.form.get("appointment_id")
    doctor_name = (request.form.get("doctor_name") or "").strip()
    rating = (request.form.get("rating") or "").strip()
    feedback_text = (request.form.get("feedback_text") or "").strip()
    health_id = session.get('health_id')

    if appointment_id and doctor_name and rating and health_id:
        try:
            db = get_db()
            cursor = db.cursor()
            cursor.execute(
                "INSERT INTO feedback (appointment_id, health_id, doctor_name, rating, feedback_text) VALUES (%s, %s, %s, %s, %s)",
                (appointment_id, health_id, doctor_name, rating, feedback_text)
            )
            cursor.execute(
                "UPDATE appointments SET visit_done = 1 WHERE id = %s AND health_id = %s",
                (appointment_id, health_id)
            )
            db.commit()
            cursor.close()
            db.close()
        except Exception as e:
            print(f"Error submitting feedback: {e}")

    return redirect(url_for('patient_dashboard'))


@app.route('/about')
def about():
    return render_template("about.html", titile="About Page")


# ---------------------------------------------------------------------------
# Waiting-room display (public TV screen)
# ---------------------------------------------------------------------------
def all_doctor_queues(cursor, qdate):
    """[(doctor_name, queue_dict), ...] for every doctor on a date."""
    cursor.execute("SELECT name FROM doctors ORDER BY name ASC")
    names = [r['name'] for r in cursor.fetchall()]
    return [(n, compute_queue(cursor, n, qdate)) for n in names]


@app.route("/queue/display")
def queue_display():
    # Public page for a TV in the waiting room. Shows token numbers only (no patient details).
    return render_template("queue_display.html")


@app.route("/api/queue/display")
def api_queue_display():
    today = date.today()
    doctors_out = []
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        for name, q in all_doctor_queues(cursor, today):
            upcoming = [r['token_no'] for r in q['order'] if r['queue_status'] in ('Waiting', 'Skipped')]
            doctors_out.append({
                "doctor": name,
                "now_serving": q['now_serving'],
                "next": upcoming[:4],
                "waiting": q['waiting_count'],
                "done": q['done_count'],
                "total": q['total'],
                "est_wait": q['waiting_count'] * q['avg_min'],
            })
        cursor.close()
        db.close()
    except Exception as e:
        print(f"Queue display error: {e}")
        return jsonify({"doctors": []}), 500

    return jsonify({
        "date_label": today.strftime('%A, %d %B %Y'),
        "updated": datetime.now().strftime('%I:%M:%S %p'),
        "doctors": doctors_out,
    })


# ---------------------------------------------------------------------------
# Free map (OpenStreetMap + Leaflet) - no API key needed
# ---------------------------------------------------------------------------
@app.route("/map")
def hospital_map():
    return render_template("map.html")


@app.route("/nearby-hospitals")
@app.route("/hospitals")
def nearby_hospitals():
    return redirect(url_for("hospital_map"))


if __name__ == '__main__':
    app.run(debug=True)
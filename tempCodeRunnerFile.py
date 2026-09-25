import random
from flask import Flask, render_template, request, redirect, url_for, session
import mysql.connector

app = Flask(__name__)
app.secret_key = 'smart_hospital_secret_key_2026'

def get_db():
    server_conn = mysql.connector.connect(
        host="127.0.0.1",
        user="root",
        password="Guru2006@"
    )
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
    
    try:
        cursor.execute("ALTER TABLE doctors ADD COLUMN unique_id VARCHAR(50) DEFAULT NULL;")
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE appointments ADD COLUMN doctor_name VARCHAR(100) DEFAULT NULL;")
    except Exception:
        pass
        
    try:
        cursor.execute("ALTER TABLE appointments ADD COLUMN visit_time VARCHAR(100) DEFAULT NULL;")
    except Exception:
        pass

    try:
        cursor.execute("""
        INSERT IGNORE INTO doctors (unique_id, name, password) 
        VALUES ('DOC-2026-1001', 'usr123', 'master'), ('DOC-2026-1002', 'demo doctor', 'master');
        """)
    except Exception:
        pass

    cursor.close()
    server_conn.close()

    return mysql.connector.connect(
        host="127.0.0.1",
        user="root",
        password="Guru2006@",
        database="hospital"
    )

def generate_health_id():
    return f"MD-2026-{random.randint(1000, 9999)}"

def generate_doctor_id():
    return f"DOC-2026-{random.randint(1000, 9999)}"

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
                return render_template("login.html", error="Invalid Doctor Name/ID or Password.", default_role="doctor")
        except Exception as e:
            return render_template("login.html", error=f"Database Error: {e}", default_role="doctor")

    return render_template("login.html", default_role="doctor")

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

@app.route("/appointments", methods=["GET", "POST"])
def appointments():
    success_message = None
    error_message = None
    health_id = session.get('health_id', '')
    doctors_list = []

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

        if not health_id or not age or not gender or not disease:
            error_message = "Please provide all required fields: Health ID, Age, Gender, and Disease/Symptoms."
        else:
            try:
                db = get_db()
                cursor = db.cursor()
                cursor.execute(
                    "INSERT INTO appointments (health_id, age, gender, disease, doctor_name) VALUES (%s, %s, %s, %s, %s)",
                    (health_id, int(age), gender, disease, doctor_name if doctor_name else None)
                )
                db.commit()
                cursor.close()
                db.close()
                success_message = f"Appointment successfully booked for Health ID: {health_id}!"
            except Exception as e:
                error_message = f"Appointment Booking Error: {e}"

    return render_template("appointments.html", health_id=health_id, doctors=doctors_list, success_message=success_message, error_message=error_message)

@app.route("/doctor/confirm-appointment", methods=["POST"])
def confirm_appointment():
    if session.get('role') != 'doctor':
        return redirect(url_for('login'))

    appointment_id = request.form.get("appointment_id")
    doctor_name = (request.form.get("doctor_name") or "").strip()
    visit_time = (request.form.get("visit_time") or "").strip()

    if appointment_id and doctor_name and visit_time:
        try:
            db = get_db()
            cursor = db.cursor()
            cursor.execute(
                "UPDATE appointments SET doctor_name = %s, visit_time = %s, status = 'Confirmed' WHERE id = %s",
                (doctor_name, visit_time, appointment_id)
            )
            db.commit()
            cursor.close()
            db.close()
        except Exception as e:
            print(f"Error confirming appointment: {e}")

    return redirect(url_for('doctor_dashboard'))

@app.route("/prescription/download/<int:appointment_id>")
def download_prescription(appointment_id):
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM appointments WHERE id = %s", (appointment_id,))
        appointment = cursor.fetchone()
        
        patient_info = None
        if appointment:
            cursor.execute("SELECT * FROM patients WHERE health_id = %s", (appointment['health_id'],))
            patient_info = cursor.fetchone()

        cursor.close()
        db.close()

        if not appointment:
            return redirect(url_for('patient_dashboard'))

        return render_template("prescription_pdf.html", appt=appointment, patient=patient_info)
    except Exception as e:
        print(f"Error downloading prescription: {e}")
        return redirect(url_for('patient_dashboard'))

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
    health_id = session.get('health_id')
    if health_id:
        try:
            db = get_db()
            cursor = db.cursor(dictionary=True)
            cursor.execute("SELECT * FROM appointments WHERE health_id = %s ORDER BY id DESC", (health_id,))
            appointments_list = cursor.fetchall()
            cursor.close()
            db.close()
        except Exception as e:
            print(f"Error fetching appointments: {e}")

    return render_template("patient_dashboard.html", appointments=appointments_list)

@app.route("/doctor/dashboard")
def doctor_dashboard():
    if session.get('role') != 'doctor':
        return redirect(url_for('login'))

    appointments_list = []
    doctors_list = []
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM appointments ORDER BY id DESC")
        appointments_list = cursor.fetchall()
        
        cursor.execute("SELECT * FROM doctors ORDER BY name ASC")
        doctors_list = cursor.fetchall()
        
        cursor.close()
        db.close()
    except Exception as e:
        print(f"Error fetching doctor data: {e}")

    return render_template("doctor_dashboard.html", appointments=appointments_list, doctors=doctors_list)

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
    doctors_list = []
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM doctors ORDER BY id DESC")
        doctors_list = cursor.fetchall()
        cursor.close()
        db.close()
    except Exception as e:
        print(f"Error fetching doctors for admin: {e}")

    return render_template("admin_dashboard.html", doctors=doctors_list)

if __name__ == '__main__':
    app.run(debug=True)
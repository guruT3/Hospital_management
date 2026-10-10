import random
from flask import Flask, render_template, request, redirect, url_for, session
import mysql.connector
import os
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

    try:
        cursor.execute("ALTER TABLE appointments ADD COLUMN visit_done TINYINT(1) NOT NULL DEFAULT 0;")
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
        cursor.execute("ALTER TABLE patients ADD COLUMN allergy_alerts TEXT;")
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

    medicine_names = request.form.getlist("medicine_name")
    dosages = request.form.getlist("dosage")
    durations = request.form.getlist("duration")

    if appointment_id and doctor_name and visit_time:
        try:
            db = get_db()
            cursor = db.cursor()
            cursor.execute(
                "UPDATE appointments SET doctor_name = %s, visit_time = %s, status = 'Confirmed' WHERE id = %s",
                (doctor_name, visit_time, appointment_id)
            )

            cursor.execute("DELETE FROM medicines WHERE appointment_id = %s", (appointment_id,))

            for i in range(len(medicine_names)):
                name = (medicine_names[i] or "").strip()
                if not name:
                    continue
                dosage = (dosages[i] if i < len(dosages) else "").strip()
                duration = (durations[i] if i < len(durations) else "").strip()
                cursor.execute(
                    "INSERT INTO medicines (appointment_id, medicine_name, dosage, duration) VALUES (%s, %s, %s, %s)",
                    (appointment_id, name, dosage, duration)
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

    return render_template("patient_dashboard.html", appointments=appointments_list, medicines_map=medicines_map)

@app.route("/doctor/dashboard")
def doctor_dashboard():
    if session.get('role') != 'doctor':
        return redirect(url_for('login'))

    appointments_list = []
    doctors_list = []
    medicines_map = {}
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM appointments ORDER BY id DESC")
        appointments_list = cursor.fetchall()

        cursor.execute("SELECT * FROM doctors ORDER BY name ASC")
        doctors_list = cursor.fetchall()

        cursor.execute("SELECT * FROM medicines ORDER BY id ASC")
        for row in cursor.fetchall():
            medicines_map.setdefault(row['appointment_id'], []).append(row)

        cursor.close()
        db.close()
    except Exception as e:
        print(f"Error fetching doctor data: {e}")

    return render_template("doctor_dashboard.html", appointments=appointments_list, doctors=doctors_list, medicines_map=medicines_map)

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

    doctors_list, patients_list, appointments_list, feedback_map = [], [], [], {}

    db = get_db()
    cursor = db.cursor(dictionary=True)

    def run(sql):
        try:
            cursor.execute(sql)
            return cursor.fetchall()
        except Exception as e:
            print(f"Admin query error: {e}\nSQL: {sql}")
            return []

    doctors_list = run("""
        SELECT d.*,
               (SELECT COUNT(*) FROM appointments a
                WHERE a.doctor_name = d.name AND a.visit_done = 1) AS visit_count
        FROM doctors d
        ORDER BY d.id DESC
    """)

    patients_list = run("""
        SELECT u.id AS user_id, u.username, p.health_id, p.name, p.phone, p.allergy_alerts
        FROM patients p
        LEFT JOIN users u ON p.user_id = u.id
        ORDER BY p.id DESC
    """)

    appointments_list = run("SELECT * FROM appointments ORDER BY id DESC")

    feedback_rows = run("""
        SELECT doctor_name,
               SUM(CASE WHEN rating = 'Good' THEN 1 ELSE 0 END) AS good_count,
               SUM(CASE WHEN rating = 'Average' THEN 1 ELSE 0 END) AS average_count,
               SUM(CASE WHEN rating = 'Poor' THEN 1 ELSE 0 END) AS poor_count,
               COUNT(*) AS total_feedback
        FROM feedback
        GROUP BY doctor_name
    """)
    feedback_map = {row['doctor_name']: row for row in feedback_rows}

    cursor.close()
    db.close()

    admin_msg = session.pop('admin_msg', None)
    admin_msg_type = session.pop('admin_msg_type', None)

    return render_template(
        "admin_dashboard.html",
        doctors=doctors_list, patients=patients_list,
        appointments=appointments_list, feedback_map=feedback_map,
        admin_msg=admin_msg, admin_msg_type=admin_msg_type
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
    return render_template("about.html",titile="About Page")

 

@app.route("/map")
def hospital_map():
    return render_template("map.html")

if __name__ == '__main__':
    app.run(debug=True)
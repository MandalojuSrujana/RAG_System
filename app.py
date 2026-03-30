from flask import Flask, render_template, request, redirect, session, url_for, send_file
import sqlite3
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.prompts import PromptTemplate
from langchain_groq import ChatGroq
from langchain.chains.retrieval_qa.base import RetrievalQA
from datetime import datetime, timedelta
# import langchain
# langchain.globals.set_verbose(False)

app = Flask(__name__)
app.secret_key = "hospital_secret"

# ----------------------
# DATABASE SETUP
# ----------------------

def init_db():
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # Existing users table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fullname TEXT,
        mobile TEXT,
        username TEXT UNIQUE,
        password TEXT
    )
    """)

    # NEW: Table to store chat messages permanently
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chat_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        question TEXT,
        answer TEXT,
        res_type TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()

init_db()


def init_appointment_db():
    conn = sqlite3.connect("appointments.db")
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS appointments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        doctor TEXT,
        appointment_date TEXT,
        appointment_time TEXT,

        UNIQUE(doctor, appointment_date, appointment_time),
        UNIQUE(username, appointment_date, appointment_time)
    )
    """)

    conn.commit()
    conn.close()

init_appointment_db()


# ----------------------
# HOSPITAL SECTION DATA
# ----------------------

sections = {

"policies": """Sunrise Multispecialty Hospital
Hospital Overview & Policies

Hospital Profile
Name: Sunrise Multispecialty Hospital
Location: Hyderabad, Telangana
Established: 2012
Type: Private Multispecialty Hospital
Capacity: 350 beds
Accreditations: NABH Certified

About the Hospital:
Sunrise Multispecialty Hospital is a patient-centric healthcare institution providing comprehensive medical services across multiple specialties. The hospital emphasizes ethical medical practices, patient safety, and advanced technology.

Vision:
To provide accessible, affordable, and world-class healthcare services.

Mission:
• Deliver quality healthcare with compassion
• Use evidence-based medical practices
• Maintain transparency in treatment & billing

Admission Policy
• Patients may be admitted through OPD, Emergency, or referral
• ID proof mandatory
• Minors must be accompanied by guardian

Discharge Policy
• Doctor approval required
• Bill must be cleared before discharge
• Summary issued within 2 hours
""",

"doctors": """Departments & Doctors

General Medicine
Dr. Ramesh Kumar – MD General Medicine (12 years experience)
Dr. Anitha Rao – Diabetes Specialist

Cardiology
Dr. Suresh Reddy – Senior Cardiologist
Dr. Kavitha Sharma – Preventive Cardiology

Orthopedics
Dr. Anita Sharma – Orthopedic Surgeon

Neurology
Dr. Vikram Rao – Neurologist

Pediatrics
Dr. Sneha Patel – Pediatrician
""",

"treatments": """Treatments & Medical Services

Diabetes Management
• Blood sugar monitoring
• Diet counseling
• Insulin therapy
• Long-term follow-ups

Cardiac Care
• ECG
• TMT
• Angioplasty
• Heart failure management

Surgical Services
• General surgery
• Laparoscopic surgery
• Emergency surgeries
• ICU monitoring
""",

"visiting": """Visiting Hours

OPD
9:00 AM – 6:00 PM

Inpatient Visiting Hours
4:00 PM – 6:00 PM

ICU Visiting
5:00 PM – 5:30 PM
Only one visitor allowed.

Emergency Services
24/7 Available
""",

"billing": """Billing & Insurance

Supported Insurance
• Star Health Insurance
• HDFC ERGO
• ICICI Lombard

Payment Modes
• Cash
• Debit/Credit Cards
• UPI
• Net Banking

Transparent itemized billing provided for all services.
""",

"guidelines": """Patient Guidelines

• Maintain silence in wards
• Masks mandatory in clinical areas
• Outside food not allowed
• Respect hospital staff
• Follow hygiene rules
""",

"emergency": """Emergency Services

24/7 Emergency Department

Facilities
• ICU
• NICU
• Ambulance Services
• In-house pharmacy

Emergency Helpline
XXX-XXX-XXXX
"""
}

# ----------------------
# LOAD VECTOR DATABASE
# ----------------------

# ================= NEW RAG INITIALIZATION =================
DB_FAISS_PATH = "data/vectorstore/db_faiss"

# Exact model from your create_memory_for_llm.py
embedding_model = HuggingFaceEmbeddings(
    model_name='sentence-transformers/all-MiniLM-L6-v2'
)

# Load the FAISS database globally
vectorstore = FAISS.load_local(
    DB_FAISS_PATH, 
    embedding_model, 
    allow_dangerous_deserialization=True
)

def get_rag_prompt():
    template = """
You are a medical assistant for Sunrise Multispecialty Hospital.
Use the following context to answer the user's question.

HOSPITAL INFORMATION: {context}
PATIENT MEDICAL HISTORY: {patient_history}

GUIDELINES:
1. If the user asks about their own past records, medications, or previous visits, look at the PATIENT MEDICAL HISTORY.
2. If they ask about hospital timings, doctors, or general policies, look at the HOSPITAL INFORMATION.
3. If the answer isn't in either, say you don't know. Do not make up facts.

Question: {question}
Answer:"""
    return PromptTemplate(
        template=template, 
        input_variables=["context", "patient_history", "question"]
    )

# ----------------------
# LOGIN PAGE
# ----------------------

@app.route("/login", methods=["GET","POST"])
@app.route("/", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = sqlite3.connect("users.db")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT fullname FROM users WHERE username=? AND password=?",
            (username, password)
        )
        user = cursor.fetchone()

        if user:
            session["user"] = username
            session["name"] = user[0]
            
            # --- FETCH EXISTING HISTORY FROM DB ---
            cursor.execute("""
                SELECT question, answer, res_type 
                FROM chat_history 
                WHERE username=? 
                ORDER BY timestamp ASC
            """, (username,))
            rows = cursor.fetchall()
            conn.close()

            # Restore the formatted history into the session
            session["chat_history"] = [{"question": r[0], "answer": r[1], "type": r[2]} for r in rows]
            
            return redirect(url_for("dashboard"))
        else:
            conn.close()
            return render_template("login.html", error="Invalid username or password")

    return render_template("login.html")

# ----------------------
# SIGNUP PAGE
# ----------------------

@app.route("/signup", methods=["GET","POST"])
def signup():

    if request.method == "POST":

        fullname = request.form["fullname"]
        mobile = request.form["mobile"]
        username = request.form["username"]
        password = request.form["password"]

        if not mobile.isdigit() or len(mobile) != 10:
            return render_template("signup.html", error="Enter valid 10 digit mobile number")

        conn = sqlite3.connect("users.db")
        cursor = conn.cursor()

        try:

            cursor.execute(
                "INSERT INTO users(fullname,mobile,username,password) VALUES(?,?,?,?)",
                (fullname,mobile,username,password)
            )

            conn.commit()

        except sqlite3.IntegrityError:
            conn.close()
            return render_template("signup.html", error="Username already exists")

        conn.close()

        return redirect("/")

    return render_template("signup.html")

# ----------------------
# DASHBOARD
# ----------------------
@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/")

    conn = sqlite3.connect("appointments.db")
    cursor = conn.cursor()
    
    # 1. Fetch ALL upcoming appointments (Removed LIMIT 1)
    cursor.execute("""
        SELECT id, doctor, appointment_date, appointment_time 
        FROM appointments 
        WHERE username=? 
        AND (
            appointment_date > date('now', 'localtime') 
            OR (appointment_date = date('now', 'localtime') AND appointment_time > time('now', 'localtime'))
        )
        ORDER BY appointment_date ASC, appointment_time ASC 
    """, (session["user"],))
    
    all_apts = cursor.fetchall()
    
    # 2. Process each appointment to see if it's cancellable (> 24h away)
    processed_appointments = []
    for apt in all_apts:
        can_cancel = False
        try:
            appt_time = datetime.strptime(f"{apt[2]} {apt[3]}", "%Y-%m-%d %H:%M")
            if appt_time - datetime.now() > timedelta(hours=24):
                can_cancel = True
        except:
            pass # Handle potential date formatting issues
            
        # Store as a dictionary for easy access in HTML
        processed_appointments.append({
            'id': apt[0],
            'doctor': apt[1],
            'date': apt[2],
            'time': apt[3],
            'show_cancel': can_cancel
        })

    conn.close()
    return render_template("dashboard.html", 
                           patient_name=session["name"], 
                           appointments=processed_appointments)

def get_patient_history_text(username):
    file_path = f"patient_records/{username}.txt"
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return "No past medical history recorded."

# ----------------------
# CHATBOT (Updated with Sidebar Section Logic)
# ----------------------

@app.route("/chat", methods=["GET","POST"])
def home():
    if "user" not in session:
        return redirect("/")

    patient_name = session["name"]
    username = session["user"]
    
    # 1. SIDEBAR CLICK LOGIC (Keep as is)
    section_key = request.args.get('section')
    if section_key:
        section_content = sections.get(section_key, "Information not found.")
        return render_template("index.html", username=patient_name, section_content=section_content)

    # 2. CHAT INPUT LOGIC
    if request.method == "POST":
        user_question = request.form["question"].strip()
        
        if user_question:
            # 1. Load the history text
            personal_history_text = "No previous medical records found."
            history_file_path = os.path.join("patient_records", f"{username}.txt")
            if os.path.exists(history_file_path):
                with open(history_file_path, "r", encoding="utf-8") as f:
                    personal_history_text = f.read().strip()

            # 2. Prepare the prompt by filling in the history NOW
            # This turns a 3-variable template into a 2-variable template (context & question)
            base_prompt = get_rag_prompt()
            partial_prompt = base_prompt.partial(patient_history=personal_history_text)

            # 3. Initialize LLM
            llm = ChatGroq(
                model_name="llama-3.3-70b-versatile",
                temperature=0.0,
                groq_api_key=os.getenv("GROQ_API_KEY") 
            )

            # 4. Create the chain using the partial_prompt
            qa_chain = RetrievalQA.from_chain_type(
                llm=llm,
                chain_type="stuff",
                retriever=vectorstore.as_retriever(search_kwargs={'k': 3}),
                return_source_documents=True,
                chain_type_kwargs={"prompt": partial_prompt} # No more "extra inputs" error
            )

            # 5. Run the chain
            response = qa_chain.invoke({"query": user_question})
            raw_result = response["result"]
            
            # ... (keep your result handling and database logic the same)
            
            # Result handling logic remains the same
            if raw_result == "INVALID_QUESTION":
                answer = "❌ Please enter a clear hospital-related question."
                res_type = "error"
            elif raw_result == "CONTACT_SUPPORT":
                answer = "ℹ️ I'm sorry, I don't have that specific information. Please contact support at XXX-XXX-XXXX."
                res_type = "info"
            else:
                answer = raw_result
                res_type = "success"

            # Database update
            conn = sqlite3.connect("users.db")
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO chat_history (username, question, answer, res_type) 
                VALUES (?, ?, ?, ?)
            """, (session["user"], user_question, answer, res_type))
            conn.commit()
            conn.close()

            # Session history update
            history = session.get("chat_history", [])
            history.append({"question": user_question, "answer": answer, "type": res_type})
            session["chat_history"] = history
            session.modified = True

    return render_template("index.html", 
                           username=patient_name, 
                           chat_history=session.get("chat_history", []))
# ----------------------
# PATIENT HISTORY
# ----------------------
import os

@app.route("/patient_history")
def patient_history():
    if "user" not in session:
        return redirect("/")

    username = session["user"]
    history_data = None
    message = None

    # Define paths
    txt_path = f"patient_records/{username}.txt"
    pdf_path = f"patient_records/{username}.pdf"

    # 1. Check for Text Record
    if os.path.exists(txt_path):
        with open(txt_path, "r", encoding="utf-8") as file:
            history_data = file.read().strip()
        
        if not history_data:
            message = "Your record is currently empty."

    # 2. If no text record, check for PDF
    elif os.path.exists(pdf_path):
        history_data = "Your medical report is available for download as a PDF."
        # Optional: You can add a flag here to show a download button in HTML
    
    # 3. If neither exists
    else:
        message = "You have no history with this hospital."

    return render_template(
        "patient_history.html",
        history=history_data,
        message=message
    )

# ----------------------
# BOOK APPOINTMENT
# ----------------------

@app.route("/appointment", methods=["GET", "POST"])
def appointment():

    if "user" not in session:
        return redirect("/")

    slots = []
    message = ""

    if request.method == "POST":

        doctor = request.form["doctor"]
        date = request.form["date"]

        slots = get_available_slots(doctor, date)

    return render_template("appointment.html", slots=slots, message=message)

@app.route("/book_slot", methods=["POST"])
def book_slot():

    if "user" not in session:
        return redirect("/")

    username = session["user"]
    doctor = request.form["doctor"]
    date = request.form["date"]
    time = request.form["time"]

    conn = sqlite3.connect("appointments.db")
    cursor = conn.cursor()

    try:
        cursor.execute("""
        INSERT INTO appointments(username, doctor, appointment_date, appointment_time)
        VALUES (?, ?, ?, ?)
        """, (username, doctor, date, time))

        conn.commit()
        message = "Appointment booked successfully!"

    except:
        message = "Slot already booked! Try another."

    conn.close()

    return render_template("appointment.html", message=message)  

#------------------------
# Generate Slots
#-----------------------

def generate_slots():

    slots = []

    # Morning 9 to 1
    start = datetime.strptime("09:00", "%H:%M")
    end = datetime.strptime("13:00", "%H:%M")

    while start < end:
        slots.append(start.strftime("%H:%M"))
        start += timedelta(minutes=30)

    # Evening 2 to 6 (skip lunch)
    start = datetime.strptime("14:00", "%H:%M")
    end = datetime.strptime("18:00", "%H:%M")

    while start < end:
        slots.append(start.strftime("%H:%M"))
        start += timedelta(minutes=30)

    return slots

#----------------------
# Available slots
#----------------------


def get_available_slots(doctor, date_str):
    # 1. Get current date and time
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    current_time_str = now.strftime("%H:%M")

    conn = sqlite3.connect("appointments.db")
    cursor = conn.cursor()

    # Get booked slots
    cursor.execute("""
        SELECT appointment_time FROM appointments
        WHERE doctor=? AND appointment_date=?
    """, (doctor, date_str))

    booked = [row[0] for row in cursor.fetchall()]
    conn.close()

    all_slots = generate_slots()

    available = []
    for slot in all_slots:
        # Check if it's already booked
        if slot not in booked:
            # If date is today, only show future times
            if date_str == today_str:
                if slot > current_time_str:
                    available.append(slot)
            # If date is in the future, show all unbooked slots
            elif date_str > today_str:
                available.append(slot)
    
    return available


# ----------------------
# CLEAR CHAT HISTORY
# ----------------------

@app.route("/clear_chat")
def clear_chat():
    if "user" not in session:
        return redirect("/")

    username = session["user"]

    # 1. Remove from Database
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM chat_history WHERE username=?", (username,))
    conn.commit()
    conn.close()

    # 2. Reset the Session list
    session["chat_history"] = []
    session.modified = True

    return redirect(url_for("home"))


from fpdf import FPDF

@app.route("/download_summary")
def download_summary():
    if "user" not in session:
        return redirect("/")

    username = session["user"]
    file_path = f"patient_records/{username}.txt"
    
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    
    pdf.cell(200, 10, txt="Sunrise Multispecialty Hospital", ln=True, align='C')
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(200, 10, txt=f"Medical History Report: {session['name']}", ln=True, align='C')
    pdf.ln(10)
    
    pdf.set_font("Arial", size=11)
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as file:
            content = file.read()
            
            # --- THE FIX STARTS HERE ---
            # This replaces special dashes, quotes, and symbols with safe versions
            content = content.replace('\u2013', '-').replace('\u2014', '-') # Dashes
            content = content.replace('\u2018', "'").replace('\u2019', "'") # Smart quotes
            content = content.replace('\u201c', '"').replace('\u201d', '"') # Smart double quotes
            
            # This final line removes any other character FPDF can't handle
            safe_content = content.encode('latin-1', 'replace').decode('latin-1')
            # ---------------------------
            
            pdf.multi_cell(0, 10, txt=safe_content)
    else:
        pdf.cell(200, 10, txt="No history records found.", ln=True)

    pdf_output = f"history_{username}.pdf"
    pdf.output(pdf_output)
    return send_file(pdf_output, as_attachment=True)

@app.route("/cancel_appointment/<int:appt_id>")
def cancel_appointment(appt_id):
    if "user" not in session:
        return redirect("/")

    conn = sqlite3.connect("appointments.db")
    conn.row_factory = sqlite3.Row # Allows accessing columns by name
    cursor = conn.cursor()

    # 1. Fetch the specific appointment
    cursor.execute("SELECT * FROM appointments WHERE id = ?", (appt_id,))
    appt = cursor.fetchone()

    if appt:
        # 2. Combine date and time strings into a single datetime object
        # Assuming format: Date '2026-03-30' and Time '10:30'
        appt_datetime_str = f"{appt['appointment_date']} {appt['appointment_time']}"
        appt_datetime = datetime.strptime(appt_datetime_str, "%Y-%m-%d %H:%M")
        
        # 3. Calculate time difference
        current_time = datetime.now()
        time_diff = appt_datetime - current_time

        # 4. Check if it's more than 24 hours away
        if time_diff > timedelta(hours=24):
            cursor.execute("DELETE FROM appointments WHERE id = ?", (appt_id,))
            conn.commit()
            message = "Success: Appointment cancelled and slot released."
        else:
            message = "Error: Cannot cancel within 24 hours of the appointment."
    else:
        message = "Error: Appointment not found."

    conn.close()
    # Redirect back to dashboard (ensure your dashboard template displays a 'message' if needed)
    return redirect(url_for("dashboard", msg=message))

from fpdf import FPDF
from flask import send_file
import io

@app.route("/download_slip/<int:appt_id>")
def download_slip(appt_id):
    if "user" not in session:
        return redirect("/")

    # 1. Get the specific appointment details
    conn = sqlite3.connect("appointments.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, doctor, appointment_date, appointment_time FROM appointments WHERE id=? AND username=?", (appt_id, session["user"]))
    appt = cursor.fetchone()
    conn.close()

    if not appt:
        return "Appointment not found", 404

    # 2. Create PDF in Memory
    pdf = FPDF()
    pdf.add_page()
    
    # --- PDF Styling ---
    pdf.set_fill_color(31, 111, 139) # Your theme color #1f6f8b
    pdf.rect(0, 0, 210, 40, 'F') # Header background
    
    pdf.set_font("Arial", 'B', 24)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 20, "SUNRISE AI HOSPITAL", ln=True, align='C')
    
    pdf.set_font("Arial", size=12)
    pdf.cell(0, 10, "Digital Appointment Token", ln=True, align='C')
    
    pdf.ln(20) # Spacer
    
    # Body Text
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, f"Token ID: #SR-{appt[0]:04d}", ln=True)
    pdf.ln(5)
    
    pdf.set_font("Arial", size=12)
    pdf.cell(0, 10, f"Patient Name: {session['name']}", ln=True)
    pdf.cell(0, 10, f"Department: {appt[1]}", ln=True)
    pdf.cell(0, 10, f"Date: {appt[2]}", ln=True)
    pdf.cell(0, 10, f"Time Slot: {appt[3]}", ln=True)
    
    pdf.ln(20)
    pdf.set_font("Arial", 'I', 10)
    pdf.multi_cell(0, 10, "Instructions:\n1. Please arrive 15 minutes before your slot.\n2. Present this digital slip at the OPD reception.\n3. Masks are mandatory within hospital premises.", align='L')
    
    # Optional: Footer
    pdf.set_y(-30)
    pdf.set_font("Arial", size=8)
    pdf.set_text_color(150, 150, 150)
    pdf.cell(0, 10, "Generated by Sunrise AI Healthcare System - 2026", align='C')

    # 3. Stream the file to the browser
    output = io.BytesIO()
    pdf_output = pdf.output()
    output.write(pdf_output)
    output.seek(0)
    
    return send_file(output, as_attachment=True, download_name=f"Appointment_Slip_{appt[0]}.pdf", mimetype='application/pdf')

@app.route('/admin_login', methods=['POST'])
def admin_login():
    username = request.form.get('admin_user')
    password = request.form.get('admin_pass')

    # FIXED CREDENTIALS
    ADMIN_USER = "admin@sunrise"
    ADMIN_PASS = "admin123"

    if username == ADMIN_USER and password == ADMIN_PASS:
        session['admin_logged_in'] = True
        return redirect('/admin_dashboard')
    else:
        return render_template('login.html', error="Invalid Admin Credentials")

@app.route('/admin_dashboard')
def admin_dashboard():
    if not session.get('admin_logged_in'):
        return redirect('/')

    # 1. Fetch appointments from appointments.db
    conn_apt = sqlite3.connect("appointments.db")
    cursor_apt = conn_apt.cursor()
    
    # Corrected column names to match your init_appointment_db() schema
    cursor_apt.execute("SELECT id, username, doctor, appointment_date, appointment_time FROM appointments ORDER BY appointment_date DESC")
    all_appointments = cursor_apt.fetchall()

    # Fetch today's count
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")
    cursor_apt.execute("SELECT COUNT(*) FROM appointments WHERE appointment_date=?", (today,))
    today_count = cursor_apt.fetchone()[0]
    conn_apt.close()

    # 2. Fetch total patient count from users.db
    conn_user = sqlite3.connect("users.db")
    cursor_user = conn_user.cursor()
    cursor_user.execute("SELECT COUNT(*) FROM users")
    total_patients = cursor_user.fetchone()[0]
    conn_user.close()

    return render_template('admin_dashboard.html', 
                           appointments=all_appointments, 
                           total_patients=total_patients, 
                           today_count=today_count)

@app.route('/admin_upload', methods=['POST'])
def admin_upload():
    if not session.get('admin_logged_in'):
        return redirect('/')

    patient_user = request.form.get('patient_username').strip()
    file = request.files.get('report_file')

    if not patient_user or not file:
        return "Missing username or file", 400

    # Ensure the directory exists
    upload_folder = "patient_records"
    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder)

    # Get the file extension (e.g., .txt or .pdf)
    file_extension = os.path.splitext(file.filename)[1]
    
    # Save as username.extension (e.g., srujana.txt)
    filename = f"{patient_user}{file_extension}"
    save_path = os.path.join(upload_folder, filename)
    
    file.save(save_path)
    
    return redirect('/admin_dashboard') # Refresh the page after upload
# ----------------------
# LOGOUT
# ----------------------

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

if __name__ == "__main__":
    # debug=True is fine, but use_reloader=False stops the infinite crashing loop
    app.run(debug=True, use_reloader=False)
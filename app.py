from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime, timedelta
from PIL import Image
import google.generativeai as genai
import os
from werkzeug.utils import secure_filename
from geopy.distance import geodesic
import json

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev'  # Change this in production
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///drainage.db'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Ensure upload directory exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Configure Google AI
genai.configure(api_key="AIzaSyBy9sEVxQY8qps0R9_tjIklTbcr3r8uaWM")

# Database Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'citizen', 'worker', 'officer'
    reports = db.relationship('Report', 
                            foreign_keys='Report.user_id',
                            backref='reporter', 
                            lazy=True)
    assigned_reports = db.relationship('Report',
                                     foreign_keys='Report.assigned_worker',
                                     backref='worker',
                                     lazy=True)

class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    image_path = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, in_progress, resolved
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_update = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    assigned_worker = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    report_count = db.Column(db.Integer, default=1)
    priority = db.Column(db.Integer, default=1)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def analyze_image(image_path, description):
    try:
        model = genai.GenerativeModel('gemini-2.0-flash')
        image = Image.open(image_path)
        prompt = (
            "You are an expert in urban infrastructure. "
            "Look at this image and answer ONLY with YES or NO on the first line: "
            "Does this image show a drainage problem (such as blocked drains, water accumulation, flooding, or damaged drainage pipes)? "
            "On the second line, briefly explain your answer."
        )
        response = model.generate_content([image, prompt])
        response_text = response.text.strip().lower()
        print(f"AI Response: {response_text}")  # Debug logging

        # Accept 'yes', 'yes.', 'yes:', 'yes -', etc.
        first_line = response_text.split('\n')[0].strip().replace(':', '').replace('-', '').replace('.', '').strip()
        return first_line.startswith('yes')
    except Exception as e:
        print(f"Error analyzing image: {e}")
        return False

def check_duplicate_reports(latitude, longitude, image_path, radius_km=0.5):
    # Find reports within radius
    nearby_reports = Report.query.all()
    for report in nearby_reports:
        distance = geodesic(
            (latitude, longitude),
            (report.latitude, report.longitude)
        ).kilometers
        if distance <= radius_km:
            # TODO: Implement image similarity check
            return report
    return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username, password=password).first()
        if user:
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid credentials')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        role = request.form['role']
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists')
            return redirect(url_for('register'))
        
        user = User(username=username, password=password, role=role)
        db.session.add(user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/report', methods=['GET', 'POST'])
@login_required
def report():
    if request.method == 'POST':
        if 'image' not in request.files:
            flash('No image uploaded')
            return redirect(request.url)
        
        file = request.files['image']
        if file.filename == '':
            flash('No selected file')
            return redirect(request.url)
        
        if file:
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            # Analyze image
            is_drainage_problem = analyze_image(filepath, request.form['description'])
            if not is_drainage_problem:
                flash('This does not appear to be a drainage problem. Please ensure the image clearly shows a drainage-related issue.')
                return redirect(request.url)
            
            # Check for duplicate reports
            duplicate = check_duplicate_reports(
                float(request.form['latitude']),
                float(request.form['longitude']),
                filepath
            )
            
            if duplicate:
                duplicate.report_count += 1
                duplicate.priority = min(duplicate.priority + 1, 5)
                db.session.commit()
                flash('Similar report found. Priority increased.')
                return redirect(url_for('dashboard'))
            
            report = Report(
                image_path=filepath,
                description=request.form['description'],
                latitude=float(request.form['latitude']),
                longitude=float(request.form['longitude']),
                user_id=current_user.id
            )
            db.session.add(report)
            db.session.commit()
            flash('Report submitted successfully')
            return redirect(url_for('dashboard'))
    
    return render_template('report.html')

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'citizen':
        reports = Report.query.filter_by(user_id=current_user.id).all()
    elif current_user.role == 'worker':
        reports = Report.query.filter_by(assigned_worker=current_user.id).all()
    else:  # officer
        reports = Report.query.all()
        workers = User.query.filter_by(role='worker').all()
        return render_template('dashboard.html', reports=reports, workers=workers)
    return render_template('dashboard.html', reports=reports)

@app.route('/update_report/<int:report_id>', methods=['POST'])
@login_required
def update_report(report_id):
    if current_user.role not in ['worker', 'officer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    report = Report.query.get_or_404(report_id)
    status = request.form['status']
    report.status = status
    report.last_update = datetime.utcnow()
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/assign_worker/<int:report_id>', methods=['POST'])
@login_required
def assign_worker(report_id):
    if current_user.role != 'officer':
        return jsonify({'error': 'Unauthorized'}), 403
    
    report = Report.query.get_or_404(report_id)
    worker_id = request.form['worker_id']
    report.assigned_worker = worker_id
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out successfully.')
    return redirect(url_for('index'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
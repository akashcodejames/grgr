# app.py
from flask import Flask, render_template, request, redirect, url_for, session, send_file
import random
import mysql.connector
import json
import io
import csv
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'timetable_generator_secret_key'

def get_course_map():
    # Fetch all courses from the database
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    query=("""
        SELECT id, name FROM courses
    """)
    cursor.execute(query)
    courses=cursor.fetchall()
    print(courses)
    # Create a dictionary to map course_id to course_name
    course_map = {str(course['id']): course['name'] for course in courses}
    print(course_map)
    return course_map

def format_batch_string(course_id, year, semester, batch_id):
    """
    Creates a consistently formatted batch string from components.
    Ensures all components are strings with proper spacing.
    """
    return f"{str(course_id)},{str(year)}, {str(semester)}, {str(batch_id)}"

def parse_batch_string(batch_string):
    """
    Parses a batch string into its components.
    Returns (course_id, year, semester, batch_id) as strings.
    """
    try:
        parts = batch_string.split(',')
        course_id = parts[0].strip()
        year = parts[1].strip()
        semester = parts[2].strip()
        batch_id = parts[3].strip()
        return course_id, year, semester, batch_id
    except Exception as e:
        print(f"Error parsing batch string '{batch_string}': {str(e)}")
        # Return default values or raise exception based on your preference
        return None, None, None, None

# Database connection function
def get_db_connection():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="toor",
        database="xyz"
    )


# Fetch subjects and teachers from the database
def fetch_subjects_and_teachers():
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    query = """
    SELECT 
        sa.course_subject_id, 
        cs.subject_code, cs.subject_name, cs.year, cs.semester, cs.batch_id, cs.course_id, cs.is_active, cs.created_at,
        td.first_name, td.last_name, td.email, td.phone, td.department, td.appointment_date, td.photo_path
    FROM subject_assignments sa
    JOIN course_subjects cs ON sa.course_subject_id = cs.id
    JOIN teacher_details td ON sa.teacher_id = td.id
    WHERE cs.is_active = 1
    """
    cursor.execute(query)
    result = cursor.fetchall()

    # Get periods configuration from the database
    subject_periods = fetch_subject_periods()

    subjects = {}
    for row in result:
        subject_name = row['subject_name']
        teacher_name = f"{row['first_name']} {row['last_name']}"

        # Use the consistent format_batch_string function
        batch_name = format_batch_string(
            row['course_id'],
            row['year'],
            row['semester'],
            row['batch_id']
        )

        subject_code = row['subject_code']
        course_subject_id = row['course_subject_id']
        teacher_details = {
            "name": teacher_name,
            "email": row['email'],
            "phone": row['phone'],
            "department": row['department'],
            "appointment_date": row['appointment_date'],
            "photo_path": row['photo_path']
        }

        if batch_name not in subjects:
            subjects[batch_name] = {}

        if subject_name not in subjects[batch_name]:
            # Use saved periods if available, otherwise use defaults
            if course_subject_id in subject_periods:
                max_periods_per_day = subject_periods[course_subject_id]['max_periods_per_day']
                max_periods_per_week = subject_periods[course_subject_id]['max_periods_per_week']
            else:
                # Default values if no configuration exists
                max_periods_per_day = 1
                max_periods_per_week = 3

            subjects[batch_name][subject_name] = {
                "subject_code": subject_code,
                "course_subject_id": course_subject_id,
                "teachers": [],
                "details": {
                    "course_id": row['course_id'],
                    "created_at": row['created_at']
                },
                "constraints": {
                    "max_periods_per_day": max_periods_per_day,
                    "max_periods_per_week": max_periods_per_week
                }
            }

        subjects[batch_name][subject_name]["teachers"].append(teacher_details)

    cursor.close()
    db.close()
    return subjects


# Timetable configuration
days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
periods_per_day = 7


# Generate initial population with empty slots allowed
def generate_initial_population(subjects, batches, population_size):
    population = []
    for _ in range(population_size):
        timetable = {batch: {day: [""] * periods_per_day for day in days} for batch in batches}
        for batch in batches:
            # Initialize subject weekly counters
            subject_counters = {subject: 0 for subject in subjects[batch]}

            # First pass: try to assign subjects without exceeding constraints
            for day in days:
                # Shuffle periods to randomize initial assignments
                period_indices = list(range(periods_per_day))
                random.shuffle(period_indices)

                for period in period_indices:
                    # Randomly decide if this period should be assigned or left empty
                    if random.random() < 0.8:  # 80% chance of assignment
                        # Find eligible subjects (not exceeding weekly limit)
                        eligible_subjects = [
                            sub for sub in subjects[batch]
                            if subject_counters[sub] < subjects[batch][sub]["constraints"]["max_periods_per_week"]
                        ]

                        if eligible_subjects:
                            subject = random.choice(eligible_subjects)
                            teacher = random.choice(subjects[batch][subject]["teachers"])["name"]
                            timetable[batch][day][period] = f"{subject} ({teacher})"
                            subject_counters[subject] += 1

        population.append(timetable)
    return population


# Fitness function with updated scoring to prefer consecutive classes and respect max periods
def fitness(timetable, subjects, batches):
    penalty = 0
    teacher_schedule = {teacher["name"]: {day: [""] * periods_per_day for day in days} for batch in subjects for sub in
                        subjects[batch] for teacher in subjects[batch][sub]["teachers"]}

    for batch in batches:
        subject_weekly_count = {sub: 0 for sub in subjects[batch]}  # Track weekly count

        # First check weekly limits
        for day in days:
            daily_subject_count = {sub: 0 for sub in subjects[batch]}
            for period in range(periods_per_day):
                entry = timetable[batch][day][period]
                if entry:  # Skip empty periods
                    subject, teacher = entry.rsplit(" (", 1)
                    teacher = teacher.rstrip(")")

                    # Count subject occurrences
                    daily_subject_count[subject] += 1
                    subject_weekly_count[subject] += 1

                    # Enforce max periods per day
                    max_periods_per_day = subjects[batch][subject]["constraints"]["max_periods_per_day"]
                    if daily_subject_count[subject] > max_periods_per_day:
                        penalty += 50  # Higher penalty for exceeding daily limit

                    # Check teacher conflicts
                    if teacher_schedule[teacher][day][period] != "":
                        penalty += 100  # Very high penalty for teacher conflicts
                    else:
                        teacher_schedule[teacher][day][period] = batch

        # Check weekly limits and penalize severely if exceeded
        for subject, count in subject_weekly_count.items():
            max_periods_per_week = subjects[batch][subject]["constraints"]["max_periods_per_week"]
            if count > max_periods_per_week:
                penalty += 200 * (count - max_periods_per_week)  # Severe penalty for exceeding weekly limits

        # Reward consecutive periods for the same subject
        for day in days:
            for period in range(periods_per_day - 1):
                current_entry = timetable[batch][day][period]
                next_entry = timetable[batch][day][period + 1]

                if current_entry and next_entry:  # Both periods have assignments
                    current_subject = current_entry.split(" (")[0]
                    next_subject = next_entry.split(" (")[0]

                    if current_subject == next_subject:
                        penalty -= 2  # Reward consecutive classes of the same subject

        # Small penalty for empty periods surrounded by non-empty ones (gaps)
        for day in days:
            for period in range(1, periods_per_day - 1):
                prev_entry = timetable[batch][day][period - 1]
                current_entry = timetable[batch][day][period]
                next_entry = timetable[batch][day][period + 1]

                if (prev_entry and next_entry) and not current_entry:
                    penalty += 1  # Small penalty for isolated empty periods

    return penalty


# Selection function
def selection(population, subjects, batches):
    # Tournament selection
    tournament_size = 3
    selected = []

    for _ in range(2):  # Select 2 parents
        tournament = random.sample(population, min(tournament_size, len(population)))
        winner = min(tournament, key=lambda x: fitness(x, subjects, batches))
        selected.append(winner)

    return selected


# Crossover function
def crossover(parent1, parent2, batches):
    child = {batch: {day: [""] * periods_per_day for day in days} for batch in batches}

    for batch in batches:
        # For each batch, randomly choose which days to inherit from which parent
        for day in days:
            if random.random() < 0.5:
                # Inherit full day from parent1
                child[batch][day] = parent1[batch][day].copy()
            else:
                # Inherit full day from parent2
                child[batch][day] = parent2[batch][day].copy()

    return child


# Mutation function with respect to constraints
def mutate(timetable, subjects, batches, mutation_rate):
    for batch in batches:
        # Check current subject counts
        subject_count = {subject: 0 for subject in subjects[batch]}

        # Count current occurrences
        for day in days:
            for period in range(periods_per_day):
                entry = timetable[batch][day][period]
                if entry:
                    subject = entry.split(" (")[0]
                    subject_count[subject] += 1

        # Mutation that respects weekly limits
        for day in days:
            for period in range(periods_per_day):
                if random.random() < mutation_rate:
                    # 25% chance to clear a period
                    if random.random() < 0.25:
                        timetable[batch][day][period] = ""
                    else:
                        # Find subjects that haven't reached weekly limit
                        available_subjects = [
                            sub for sub in subjects[batch]
                            if subject_count[sub] < subjects[batch][sub]["constraints"]["max_periods_per_week"]
                        ]

                        if available_subjects:
                            # Select a subject that hasn't reached its limit
                            subject = random.choice(available_subjects)
                            teacher = random.choice(subjects[batch][subject]["teachers"])["name"]

                            # If this period already had a subject, decrement its count
                            if timetable[batch][day][period]:
                                old_subject = timetable[batch][day][period].split(" (")[0]
                                subject_count[old_subject] -= 1

                            # Assign new subject and increment its count
                            timetable[batch][day][period] = f"{subject} ({teacher})"
                            subject_count[subject] += 1

    return timetable


# Optimizer to ensure weekly constraints are strictly met
def optimize_timetable(timetable, subjects, batches):
    for batch in batches:
        # Count current subject occurrences
        subject_count = {subject: 0 for subject in subjects[batch]}

        for day in days:
            for period in range(periods_per_day):
                entry = timetable[batch][day][period]
                if entry:
                    subject = entry.split(" (")[0]
                    subject_count[subject] += 1

        # Remove excess assignments that exceed weekly limits
        for subject, count in subject_count.items():
            max_per_week = subjects[batch][subject]["constraints"]["max_periods_per_week"]

            # If we've exceeded the weekly limit
            if count > max_per_week:
                excess = count - max_per_week

                # Find and remove excess periods
                for day in reversed(days):  # Start from Friday
                    for period in reversed(range(periods_per_day)):  # Start from last period
                        entry = timetable[batch][day][period]
                        if entry and entry.split(" (")[0] == subject:
                            timetable[batch][day][period] = ""  # Clear the period
                            excess -= 1
                            if excess == 0:
                                break
                    if excess == 0:
                        break

        # Try to arrange consecutive periods for the same subject
        for day in days:
            # Group consecutive empty periods
            empty_periods = []
            start = -1

            for p in range(periods_per_day):
                if not timetable[batch][day][p]:  # Empty period
                    if start == -1:
                        start = p
                else:  # Non-empty period
                    if start != -1:
                        empty_periods.append((start, p - 1))
                        start = -1

            # Don't forget the last group if it extends to the end
            if start != -1:
                empty_periods.append((start, periods_per_day - 1))

            # Now try to place subjects in consecutive periods
            for subject in subjects[batch]:
                # Check if we can still add more periods of this subject
                current_count = sum(1 for d in days for p in range(periods_per_day)
                                    if timetable[batch][d][p] and timetable[batch][d][p].split(" (")[0] == subject)

                max_count = subjects[batch][subject]["constraints"]["max_periods_per_week"]

                if current_count < max_count:
                    # See if we can place this subject near existing occurrences
                    for p in range(periods_per_day - 1):
                        if (timetable[batch][day][p] and
                                timetable[batch][day][p].split(" (")[0] == subject and
                                not timetable[batch][day][p + 1]):

                            # Found subject followed by empty period
                            teacher = random.choice(subjects[batch][subject]["teachers"])["name"]
                            timetable[batch][day][p + 1] = f"{subject} ({teacher})"
                            current_count += 1

                            if current_count >= max_count:
                                break

                    # Also check for empty period followed by this subject
                    if current_count < max_count:
                        for p in range(1, periods_per_day):
                            if (not timetable[batch][day][p - 1] and
                                    timetable[batch][day][p] and
                                    timetable[batch][day][p].split(" (")[0] == subject):

                                # Found empty period followed by subject
                                teacher = random.choice(subjects[batch][subject]["teachers"])["name"]
                                timetable[batch][day][p - 1] = f"{subject} ({teacher})"
                                current_count += 1

                                if current_count >= max_count:
                                    break

    return timetable


# Main GA function
def create_timetable(population_size=10, generations=100, mutation_rate=0.1):
    subjects = fetch_subjects_and_teachers()
    batches = list(subjects.keys())

    if not batches:
        return None, "No active batches found in the database."

    population = generate_initial_population(subjects, batches, population_size)
    best_fitness = float('inf')
    best_timetable = None

    for generation in range(1, generations + 1):
        new_population = []
        for _ in range(population_size):
            parent1, parent2 = selection(population, subjects, batches)
            child = crossover(parent1, parent2, batches)
            child = mutate(child, subjects, batches, mutation_rate)
            new_population.append(child)

        population = new_population
        current_best = min(population, key=lambda x: fitness(x, subjects, batches))
        current_fitness = fitness(current_best, subjects, batches)

        if current_fitness < best_fitness:
            best_fitness = current_fitness
            best_timetable = current_best

        if best_fitness <= 0:
            break

    # Final optimization to ensure constraints are strictly met
    best_timetable = optimize_timetable(best_timetable, subjects, batches)

    return best_timetable, batches, subjects


# Analyze timetable
def analyze_timetable(timetable, subjects, batches):
    analysis = {}

    for batch in batches:
        # Skip if the batch is not in the timetable
        if batch not in timetable:
            continue

        # Try to find the corresponding batch in subjects
        subject_batch = batch
        if batch not in subjects:
            # Try to find a matching batch with different formatting
            batch_parts = parse_batch_string(batch)
            if None in batch_parts:
                continue  # Skip this batch if parsing fails

            for sb in subjects.keys():
                sb_parts = parse_batch_string(sb)
                if None in sb_parts:
                    continue

                # Compare the components
                if batch_parts == sb_parts:
                    subject_batch = sb
                    break

            # If we still can't find it, skip this batch
            if subject_batch not in subjects:
                continue

        analysis[batch] = {
            "subjects": {},
            "empty_periods": 0
        }

        subject_counts = {subject: {"daily": {day: 0 for day in days}, "total": 0}
                          for subject in subjects[subject_batch]}

        empty_count = 0

        for day in days:
            for period in range(periods_per_day):
                entry = timetable[batch][day][period]
                if entry:
                    # Extract subject name safely
                    try:
                        if " (" in entry and ")" in entry:
                            subject, _ = entry.rsplit(" (", 1)

                            # Check if this subject exists in the subjects dictionary
                            if subject in subject_counts:
                                subject_counts[subject]["daily"][day] += 1
                                subject_counts[subject]["total"] += 1
                            else:
                                # Unknown subject
                                print(f"Unknown subject in timetable: {subject}")
                        else:
                            print(f"Invalid entry format: {entry}")
                    except Exception as e:
                        print(f"Error processing entry {entry}: {str(e)}")
                else:
                    empty_count += 1

        analysis[batch]["empty_periods"] = empty_count

        for subject, counts in subject_counts.items():
            try:
                constraints = subjects[subject_batch][subject]["constraints"]
                analysis[batch]["subjects"][subject] = {
                    "weekly_total": counts["total"],
                    "max_weekly": constraints["max_periods_per_week"],
                    "daily_counts": {},
                    "compliant": True
                }

                for day in days:
                    daily_count = counts["daily"][day]
                    max_daily = constraints["max_periods_per_day"]
                    is_compliant = daily_count <= max_daily
                    if not is_compliant:
                        analysis[batch]["subjects"][subject]["compliant"] = False

                    analysis[batch]["subjects"][subject]["daily_counts"][day] = {
                        "count": daily_count,
                        "max": max_daily,
                        "compliant": is_compliant
                    }
            except Exception as e:
                print(f"Error analyzing subject {subject}: {str(e)}")
                # Skip this subject

    return analysis


# Routes
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/generate', methods=['POST'])
def generate():
    population_size = int(request.form.get('population_size', 10))
    generations = int(request.form.get('generations', 100))
    mutation_rate = float(request.form.get('mutation_rate', 0.1))

    timetable, batches, subjects = create_timetable(
        population_size=population_size,
        generations=generations,
        mutation_rate=mutation_rate
    )

    if timetable is None:
        return render_template('index.html', error=batches)

    analysis = analyze_timetable(timetable, subjects, batches)

    # Store timetable in session for download
    session['timetable'] = json.dumps(timetable)
    session['batches'] = json.dumps(batches)

    return render_template(
        'results.html',
        timetable=timetable,
        batches=batches,
        days=days,
        periods_per_day=periods_per_day,
        analysis=analysis
    )


@app.route('/download_csv')
def download_csv():
    timetable = json.loads(session.get('timetable', '{}'))
    batches = json.loads(session.get('batches', '[]'))

    if not timetable or not batches:
        return redirect(url_for('index'))

    # Create in-memory CSV file
    output = io.StringIO()
    writer = csv.writer(output)

    for batch in batches:
        writer.writerow([f"Timetable for {batch}"])
        writer.writerow(
            ["Day", "Period 1", "Period 2", "Period 3", "Lunch", "Period 4", "Period 5", "Period 6", "Period 7"])

        for day in days:
            row = [day]
            for i in range(periods_per_day):
                if i == 3:  # Add lunch break after period 3
                    row.append("LUNCH")
                row.append(timetable[batch][day][i] if timetable[batch][day][i] else "FREE")
            writer.writerow(row)

        # Add empty row between batches
        writer.writerow([])

    output.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'timetable_{timestamp}.csv'
    )


def fetch_all_subjects():
    """Fetch all active subjects from the database with their details"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    query = """
    SELECT 
        cs.id, cs.subject_code, cs.subject_name, cs.year, cs.semester, 
        cs.batch_id, cs.course_id, cs.is_active, cs.created_at
    FROM course_subjects cs
    WHERE cs.is_active = 1
    ORDER BY cs.year, cs.semester, cs.batch_id, cs.subject_name
    """
    cursor.execute(query)
    subjects = cursor.fetchall()

    cursor.close()
    db.close()
    return subjects


def fetch_subject_periods():
    """Fetch period configurations for all subjects"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    query = """
    SELECT 
        sp.id, sp.course_subject_id, sp.max_periods_per_day, sp.max_periods_per_week,
        sp.created_at, sp.updated_at
    FROM subject_periods sp
    """
    cursor.execute(query)
    results = cursor.fetchall()

    # Convert to dictionary for easy access by subject ID
    periods_dict = {}
    for row in results:
        periods_dict[row['course_subject_id']] = {
            'max_periods_per_day': row['max_periods_per_day'],
            'max_periods_per_week': row['max_periods_per_week'],
            'created_at': row['created_at'],
            'updated_at': row['updated_at']
        }

    cursor.close()
    db.close()
    return periods_dict


def save_subject_periods(subject_id, max_periods_per_day, max_periods_per_week):
    """Save or update period configuration for a subject"""
    db = get_db_connection()
    cursor = db.cursor()

    # Check if entry exists
    check_query = "SELECT id FROM subject_periods WHERE course_subject_id = %s"
    cursor.execute(check_query, (subject_id,))
    result = cursor.fetchone()

    try:
        if result:
            # Update existing entry
            update_query = """
            UPDATE subject_periods 
            SET max_periods_per_day = %s, max_periods_per_week = %s 
            WHERE course_subject_id = %s
            """
            cursor.execute(update_query, (max_periods_per_day, max_periods_per_week, subject_id))
        else:
            # Create new entry
            insert_query = """
            INSERT INTO subject_periods (course_subject_id, max_periods_per_day, max_periods_per_week)
            VALUES (%s, %s, %s)
            """
            cursor.execute(insert_query, (subject_id, max_periods_per_day, max_periods_per_week))

        db.commit()
        success = True
    except Exception as e:
        db.rollback()
        print(f"Database error: {e}")
        success = False
    finally:
        cursor.close()
        db.close()

    return success


# Modify your existing fetch_subjects_and_teachers function to use the configured periods



# Add these new routes to your Flask application
@app.route('/configure_periods')
def configure_periods():
    """Render the subject periods configuration page"""
    subjects = fetch_all_subjects()
    subject_periods = fetch_subject_periods()

    # Group subjects by batch
    batches = {}
    for subject in subjects:
        batch_key = f"{subject['course_id']},{subject['year']}, {subject['semester']}, {subject['batch_id']}"

        if batch_key not in batches:
            batches[batch_key] = []

        # Add period info if available, otherwise use defaults
        if subject['id'] in subject_periods:
            subject['max_periods_per_day'] = subject_periods[subject['id']]['max_periods_per_day']
            subject['max_periods_per_week'] = subject_periods[subject['id']]['max_periods_per_week']
        else:
            subject['max_periods_per_day'] = 1  # Default value
            subject['max_periods_per_week'] = 3  # Default value

        batches[batch_key].append(subject)

    return render_template('configure_periods.html', batches=batches)


@app.route('/save_periods', methods=['POST'])
def save_periods():
    """Save the period configuration for subjects"""
    if request.method == 'POST':
        processed_subjects = set()

        for key, value in request.form.items():
            if key.startswith('subject_'):
                parts = key.split('_')
                if len(parts) == 3 and parts[2] in ['day', 'week']:
                    subject_id = int(parts[1])

                    # Process each subject only once
                    if subject_id not in processed_subjects:
                        processed_subjects.add(subject_id)

                        # Get both values
                        day_key = f'subject_{subject_id}_day'
                        week_key = f'subject_{subject_id}_week'

                        if day_key in request.form and week_key in request.form:
                            try:
                                max_day = int(request.form[day_key])
                                max_week = int(request.form[week_key])

                                # Validate the input
                                if max_day < 1:
                                    max_day = 1
                                if max_day > 7:
                                    max_day = 7
                                if max_week < max_day:
                                    max_week = max_day
                                if max_week > 35:
                                    max_week = 35

                                # Save to database
                                save_subject_periods(subject_id, max_day, max_week)
                            except ValueError:
                                # Handle invalid input
                                continue

        return redirect(url_for('configure_periods', success=True))


# Add this new route to app.py
@app.route('/save_timetable', methods=['POST'])
def save_timetable():
    """Save the modified timetable to the database"""
    if request.method == 'POST':
        try:
            # Get the timetable data from the form
            timetable_data = json.loads(request.form.get('timetable_data', '{}'))

            if not timetable_data:
                return redirect(url_for('index', error="No timetable data received"))

            # Connect to the database
            db = get_db_connection()
            cursor = db.cursor()

            # First, clear existing timetable entries
            clear_query = "DELETE FROM timetable_assignments WHERE 1=1"
            cursor.execute(clear_query)

            # Insert the new timetable data
            for batch, batch_data in timetable_data.items():
                # Parse batch information
                batch_parts = parse_batch_string(batch)
                if None in batch_parts:
                    continue  # Skip this batch if parsing fails

                course_id, year, semester, batch_id = batch_parts

                for day, day_data in batch_data.items():
                    for period, entry in enumerate(day_data):
                        if entry:  # Only save non-empty entries
                            try:
                                # Split the subject and teacher
                                if " (" in entry and ")" in entry:
                                    subject, teacher = entry.rsplit(' (', 1)
                                    teacher = teacher.rstrip(')')
                                else:
                                    # Handle case where format is not as expected
                                    print(f"Skipping entry with invalid format: {entry}")
                                    continue

                                # Get subject_id and teacher_id from the database
                                subject_query = """
                                SELECT cs.id 
                                FROM course_subjects cs 
                                WHERE cs.subject_name = %s AND cs.course_id = %s AND cs.year = %s 
                                AND cs.semester = %s AND cs.batch_id = %s AND cs.is_active = 1
                                """
                                cursor.execute(subject_query, (subject, course_id, year, semester, batch_id))
                                subject_result = cursor.fetchone()

                                if not subject_result:
                                    print(f"Subject not found: {subject} for batch {batch}")
                                    continue  # Skip if subject not found

                                subject_id = subject_result[0]

                                teacher_query = """
                                SELECT td.id 
                                FROM teacher_details td 
                                WHERE CONCAT(td.first_name, ' ', td.last_name) = %s
                                """
                                cursor.execute(teacher_query, (teacher,))
                                teacher_result = cursor.fetchone()

                                if not teacher_result:
                                    print(f"Teacher not found: {teacher}")
                                    continue  # Skip if teacher not found

                                teacher_id = teacher_result[0]

                                # Save the assignment
                                insert_query = """
                                INSERT INTO timetable_assignments 
                                (course_id, year, semester, batch_id, day, period, subject_id, teacher_id, created_at)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                                """
                                cursor.execute(insert_query, (
                                    course_id, year, semester, batch_id, day, period, subject_id, teacher_id
                                ))
                            except Exception as e:
                                print(f"Error processing entry {entry}: {str(e)}")
                                continue  # Skip this entry and continue with others

            # Commit the changes
            db.commit()
            cursor.close()
            db.close()

            # Store success message in session
            session['success_message'] = "Timetable successfully saved!"

            return redirect(url_for('view_saved_timetable'))

        except Exception as e:
            # Handle errors with more detailed information
            import traceback
            error_msg = f"Error saving timetable: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            return render_template('index.html', error=f"Error saving timetable: {str(e)}")


@app.route('/view_saved_timetable')
def view_saved_timetable():
    """Retrieve and display the saved timetable"""
    try:
        # Connect to the database
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        # Get unique batches
        batch_query = """
        SELECT DISTINCT course_id, year, semester, batch_id 
        FROM timetable_assignments
        ORDER BY year, semester, batch_id
        """
        cursor.execute(batch_query)
        batch_results = cursor.fetchall()

        if not batch_results:
            # Instead of using saved_timetable.html, use index.html with a message
            return render_template('index.html',
                                   message="No saved timetable found. Please generate a new timetable.")

        # Build batch strings - ensure consistent formatting
        batches = []
        for batch in batch_results:
            # Convert all values to strings to avoid type issues
            course_id = str(batch['course_id'])
            year = str(batch['year'])
            semester = str(batch['semester'])
            batch_id = str(batch['batch_id'])
            batch_str = f"{course_id},{year}, {semester}, {batch_id}"
            batches.append(batch_str)

        # Initialize timetable structure
        timetable = {batch: {day: [""] * periods_per_day for day in days} for batch in batches}

        # Get timetable assignments
        assignment_query = """
        SELECT 
            ta.course_id, ta.year, ta.semester, ta.batch_id, ta.day, ta.period,
            cs.subject_name, CONCAT(td.first_name, ' ', td.last_name) as teacher_name
        FROM timetable_assignments ta
        JOIN course_subjects cs ON ta.subject_id = cs.id
        JOIN teacher_details td ON ta.teacher_id = td.id
        ORDER BY ta.year, ta.semester, ta.batch_id, FIELD(ta.day, 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'), ta.period
        """
        cursor.execute(assignment_query)
        assignments = cursor.fetchall()

        # Fill the timetable with consistent string formatting
        for assignment in assignments:
            # Convert all values to strings to ensure consistency
            course_id = str(assignment['course_id'])
            year = str(assignment['year'])
            semester = str(assignment['semester'])
            batch_id = str(assignment['batch_id'])
            batch_str = f"{course_id},{year}, {semester}, {batch_id}"

            day = assignment['day']
            period = assignment['period']
            subject = assignment['subject_name']
            teacher = assignment['teacher_name']

            # Make sure period is an integer index
            period_idx = int(period)
            if 0 <= period_idx < periods_per_day:
                timetable[batch_str][day][period_idx] = f"{subject} ({teacher})"

        cursor.close()
        db.close()

        # Get success message from session
        success_message = session.pop('success_message', None)

        # Get subjects for analysis
        subjects = fetch_subjects_and_teachers()

        # Try to analyze the timetable, if possible
        try:
            analysis = analyze_timetable(timetable, subjects, batches)
        except Exception as e:
            print(f"Error analyzing timetable: {str(e)}")
            analysis = {}  # Use empty analysis if there's an error

        return render_template('results.html',
                               timetable=timetable,
                               batches=batches,
                               days=days,
                               periods_per_day=periods_per_day,
                               analysis=analysis,
                               success_message=success_message,
                               viewing_saved=True)

    except Exception as e:
        # Handle errors and provide more detailed error message
        import traceback
        error_msg = f"Error viewing saved timetable: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        return render_template('index.html', error=f"Error viewing saved timetable: {str(e)}")


# Add these new routes to app.py

@app.route('/edit_timetable')
def edit_timetable():
    """Retrieve and display the saved timetable in an editable format"""
    try:
        # Connect to the database
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        # Get unique batches
        batch_query = """
        SELECT DISTINCT course_id, year, semester, batch_id 
        FROM timetable_assignments
        ORDER BY year, semester, batch_id
        """
        cursor.execute(batch_query)
        batch_results = cursor.fetchall()

        if not batch_results:
            return render_template('index.html',
                                   message="No saved timetable found. Please generate a new timetable.")

        # Build batch strings
        batches = []
        for batch in batch_results:
            # Convert all values to strings to avoid type issues
            course_id = str(batch['course_id'])
            year = str(batch['year'])
            semester = str(batch['semester'])
            batch_id = str(batch['batch_id'])
            batch_str = format_batch_string(course_id, year, semester, batch_id)
            batches.append(batch_str)

        # Initialize timetable structure
        timetable = {batch: {day: [""] * periods_per_day for day in days} for batch in batches}

        # Get timetable assignments
        assignment_query = """
        SELECT 
            ta.course_id, ta.year, ta.semester, ta.batch_id, ta.day, ta.period,
            cs.subject_name, cs.id as subject_id, 
            CONCAT(td.first_name, ' ', td.last_name) as teacher_name, td.id as teacher_id
        FROM timetable_assignments ta
        JOIN course_subjects cs ON ta.subject_id = cs.id
        JOIN teacher_details td ON ta.teacher_id = td.id
        ORDER BY ta.year, ta.semester, ta.batch_id, FIELD(ta.day, 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'), ta.period
        """
        cursor.execute(assignment_query)
        assignments = cursor.fetchall()

        # Fill the timetable
        for assignment in assignments:
            batch_str = format_batch_string(
                str(assignment['course_id']),
                str(assignment['year']),
                str(assignment['semester']),
                str(assignment['batch_id'])
            )

            day = assignment['day']
            period = int(assignment['period'])
            subject = assignment['subject_name']
            teacher = assignment['teacher_name']
            subject_id = assignment['subject_id']
            teacher_id = assignment['teacher_id']

            if 0 <= period < periods_per_day:
                # Include subject_id and teacher_id for editing purposes
                timetable[batch_str][day][period] = {
                    "display": f"{subject} ({teacher})",
                    "subject_name": subject,
                    "teacher_name": teacher,
                    "subject_id": subject_id,
                    "teacher_id": teacher_id
                }

        # Get all available subjects and their assigned teachers for each batch
        all_subjects = {}

        for batch in batches:
            course_id, year, semester, batch_id = parse_batch_string(batch)

            subject_query = """
            SELECT 
                cs.id as subject_id, cs.subject_name, cs.subject_code,
                td.id as teacher_id, CONCAT(td.first_name, ' ', td.last_name) as teacher_name
            FROM course_subjects cs
            JOIN subject_assignments sa ON cs.id = sa.course_subject_id
            JOIN teacher_details td ON sa.teacher_id = td.id
            WHERE cs.course_id = %s AND cs.year = %s AND cs.semester = %s AND cs.batch_id = %s AND cs.is_active = 1
            ORDER BY cs.subject_name, teacher_name
            """
            cursor.execute(subject_query, (course_id, year, semester, batch_id))
            subject_results = cursor.fetchall()

            # Group subjects with their assigned teachers
            batch_subjects = {}
            for row in subject_results:
                subject_name = row['subject_name']
                if subject_name not in batch_subjects:
                    batch_subjects[subject_name] = {
                        "subject_id": row['subject_id'],
                        "subject_code": row['subject_code'],
                        "teachers": []
                    }

                batch_subjects[subject_name]["teachers"].append({
                    "teacher_id": row['teacher_id'],
                    "teacher_name": row['teacher_name']
                })

            all_subjects[batch] = batch_subjects

        cursor.close()
        db.close()
        course_map = get_course_map()
        print(course_map)
        session['timetable'] = json.dumps(timetable)
        return render_template('edit_timetable.html',
                               timetable=timetable,
                               batches=batches,
                               days=days,
                               periods_per_day=periods_per_day,
                               all_subjects=all_subjects,
                               course_map = course_map)

    except Exception as e:
        import traceback
        error_msg = f"Error loading editable timetable: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        return render_template('index.html', error=f"Error loading editable timetable: {str(e)}")


@app.route('/update_timetable', methods=['POST'])
def update_timetable():
    """Save the modified timetable to the database"""
    if request.method == 'POST':
        try:
            # Get the timetable data from the form
            timetable_data = json.loads(request.form.get('timetable_data', '{}'))

            if not timetable_data:
                return redirect(url_for('index', error="No timetable data received"))

            # Connect to the database
            db = get_db_connection()
            cursor = db.cursor()

            # First, clear existing timetable entries
            clear_query = "DELETE FROM timetable_assignments WHERE 1=1"
            cursor.execute(clear_query)

            # Insert the new timetable data
            for batch, batch_data in timetable_data.items():
                # Parse batch information
                course_id, year, semester, batch_id = parse_batch_string(batch)

                for day, day_data in batch_data.items():
                    for period, entry in enumerate(day_data):
                        if entry and entry.get('subject_id') and entry.get('teacher_id'):
                            # Insert the assignment using the subject_id and teacher_id
                            insert_query = """
                            INSERT INTO timetable_assignments 
                            (course_id, year, semester, batch_id, day, period, subject_id, teacher_id, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                            """
                            cursor.execute(insert_query, (
                                course_id, year, semester, batch_id, day, period,
                                entry['subject_id'], entry['teacher_id']
                            ))

            # Commit the changes
            db.commit()
            cursor.close()
            db.close()

            # Store success message in session
            session['success_message'] = "Timetable successfully updated!"

            return redirect(url_for('view_saved_timetable'))

        except Exception as e:
            import traceback
            error_msg = f"Error updating timetable: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            return render_template('index.html', error=f"Error updating timetable: {str(e)}")


PERIOD_TIMES = [
    "09:00 - 09:55",
    "10:00 - 10:55",
    "11:00 - 11:55",
    "12:00 - 12:55",
    "13:30 - 14:25",
    "14:30 - 15:25"
]


@app.route('/print_timetable')
def print_timetable():
    # Get query parameters for batch selection (optional, can default to first batch)
    course_id = request.args.get('course_id')
    year = request.args.get('year')
    semester = request.args.get('semester')
    batch_id = request.args.get('batch_id')

    # If no parameters provided, use first batch
    if not all([course_id, year, semester, batch_id]):
        # Get the first batch from the database
        query = """
            SELECT DISTINCT course_id, year, semester, batch_id 
            FROM timetable_assignments 
            ORDER BY course_id, year, semester, batch_id 
            LIMIT 1
        """
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute(query)
        result = cursor.fetchone()

        if result:
            course_id = result['course_id']
            year = result['year']
            semester = result['semester']
            batch_id = result['batch_id']

    # Get course name
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT name FROM courses WHERE id = %s", (course_id,))
    course_result = cursor.fetchone()
    course_name = course_result['name'] if course_result else "Unknown Course"

    # Get days and periods_per_day (assuming these are constants or configuration)
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    periods_per_day = 7  # Adjust as needed

    # Get timetable data
    timetable = {day: [None] * periods_per_day for day in days}
    query = """
        SELECT ta.day, ta.period, ta.subject_id, ta.teacher_id, 
               cs.subject_name, cs.subject_code,
               CONCAT(td.first_name, ' ', td.last_name) as teacher_name
        FROM timetable_assignments ta
        JOIN course_subjects cs ON ta.subject_id = cs.id
        JOIN teacher_details td ON ta.teacher_id = td.id
        WHERE ta.course_id = %s AND ta.year = %s AND ta.semester = %s AND ta.batch_id = %s
    """

    cursor.execute(query, (course_id, year, semester, batch_id))
    assignments = cursor.fetchall()

    for assignment in assignments:
        day = assignment['day']
        period = int(assignment['period'])  # Ensure period is an integer
        
        # Skip if day is not in our days list
        if day not in timetable:
            continue
            
        # Skip if period is out of range
        if period < 0 or period >= periods_per_day:
            continue
            
        timetable[day][period] = {
            'subject_id': assignment['subject_id'],
            'teacher_id': assignment['teacher_id'],
            'subject_name': assignment['subject_name'],
            'subject_code': assignment['subject_code'],
            'teacher_name': assignment['teacher_name']
        }

    # Get subject summary (unique subject-teacher combinations)
    subjects = []
    subject_dict = {}

    for day in days:
        for period in range(periods_per_day):
            if timetable[day][period]:
                subject_id = timetable[day][period]['subject_id']
                if subject_id not in subject_dict:
                    subject_dict[subject_id] = {
                        'subject_name': timetable[day][period]['subject_name'],
                        'subject_code': timetable[day][period]['subject_code'],
                        'teacher_name': timetable[day][period]['teacher_name']
                    }

    subjects = list(subject_dict.values())

    # Sort subjects by name
    subjects.sort(key=lambda x: x['subject_name'])

    # Get current date for footer
    current_date = datetime.now().strftime("%d-%m-%Y")
    
    # Close the database connection
    cursor.close()
    db.close()

    return render_template(
        'print_timetable.html',
        course_name=course_name,
        year=year,
        semester=semester,
        batch_id=batch_id,
        days=days,
        periods_per_day=periods_per_day,
        period_times=PERIOD_TIMES,
        timetable=timetable,
        subjects=subjects,
        current_date=current_date
    )

if __name__ == '__main__':
    app.run(debug=True)
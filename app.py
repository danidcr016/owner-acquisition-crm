from flask_sqlalchemy import SQLAlchemy
from flask import Flask, render_template, request, redirect, session
from datetime import datetime
from sqlalchemy import inspect, text


app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///database.db"

# Secret key used by Flask sessions
app.secret_key = "owner-acquisition-secret-key"

db = SQLAlchemy(app)


# =========================
# LOGIN
# =========================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form["username"]
        password = request.form["password"]

        if username == "jeremy" and password == "jeremy2004":

            session["logged_in"] = True
            session["username"] = username

            return redirect("/")

        return render_template(
            "login.html",
            error="Invalid username or password."
        )

    return render_template("login.html")


# =========================
# LOGOUT
# =========================

@app.route("/logout")
def logout():

    session.clear()

    return redirect("/login")


# =========================
# LEAD MODEL
# =========================

class Lead(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    name = db.Column(
        db.String(100)
    )

    phone = db.Column(
        db.String(20)
    )

    email = db.Column(
        db.String(100)
    )

    city = db.Column(
        db.String(100)
    )

    source = db.Column(
        db.String(50)
    )

    status = db.Column(
        db.String(50)
    )

    notes = db.Column(
        db.Text
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    def __repr__(self):

        return f"<Lead {self.name}>"


# =========================
# FOLLOW-UP MODEL
# =========================

class FollowUp(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    lead_id = db.Column(
        db.Integer,
        db.ForeignKey("lead.id"),
        nullable=False
    )

    type = db.Column(
        db.String(50),
        nullable=False
    )

    title = db.Column(
        db.String(200),
        nullable=False
    )

    due_date = db.Column(
        db.DateTime,
        nullable=False
    )

    notes = db.Column(
        db.Text
    )

    completed = db.Column(
        db.Boolean,
        default=False
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    lead = db.relationship(
        "Lead",
        backref=db.backref(
            "follow_ups",
            lazy=True
        )
    )

    def __repr__(self):

        return f"<FollowUp {self.title}>"


# =========================
# DASHBOARD
# =========================

@app.route("/")
def home():

    if not session.get("logged_in"):
        return redirect("/login")

    total_leads = Lead.query.count()

    new_leads = Lead.query.filter_by(
        status="NEW"
    ).count()

    contacted_leads = Lead.query.filter_by(
        status="CONTACTED"
    ).count()

    interested_leads = Lead.query.filter_by(
        status="INTERESTED"
    ).count()

    onboarding_leads = Lead.query.filter_by(
        status="ONBOARDING"
    ).count()

    active_leads = Lead.query.filter_by(
        status="ACTIVE"
    ).count()

    lost_leads = Lead.query.filter_by(
        status="LOST"
    ).count()

    return render_template(

        "index.html",

        total_leads=total_leads,

        new_leads=new_leads,

        contacted_leads=contacted_leads,

        interested_leads=interested_leads,

        onboarding_leads=onboarding_leads,

        active_leads=active_leads,

        lost_leads=lost_leads

    )


# =========================
# ADD LEAD
# =========================

@app.route(
    "/add-lead",
    methods=["GET", "POST"]
)
def add_lead():

    if not session.get("logged_in"):
        return redirect("/login")

    if request.method == "POST":

        lead = Lead(

            name=request.form["name"],

            phone=request.form["phone"],

            email=request.form["email"],

            city=request.form["city"],

            source=request.form["source"],

            status=request.form["status"],

            notes=request.form["notes"],

            created_at=datetime.utcnow()

        )

        db.session.add(lead)

        db.session.commit()

        return render_template(
            "success.html"
        )

    return render_template(
        "add_lead.html"
    )


# =========================
# VIEW LEADS
# SEARCH + FILTER
# =========================

@app.route("/leads")
def leads():

    if not session.get("logged_in"):
        return redirect("/login")

    search = request.args.get(
        "search",
        ""
    ).strip()

    status_filter = request.args.get(
        "status",
        ""
    ).strip()

    query = Lead.query

    # SEARCH BY NAME

    if search:

        query = query.filter(
            Lead.name.ilike(
                f"%{search}%"
            )
        )

    # FILTER BY STATUS

    if status_filter:

        query = query.filter(
            Lead.status == status_filter
        )

    # ORDER BY ID

    query = query.order_by(
        Lead.id.asc()
    )

    all_leads = query.all()

    return render_template(

        "leads.html",

        leads=all_leads,

        search=search,

        status_filter=status_filter

    )


# =========================
# UPDATE STATUS
# =========================

@app.route(
    "/update-status/<int:id>",
    methods=["POST"]
)
def update_status(id):

    if not session.get("logged_in"):
        return redirect("/login")

    lead = Lead.query.get_or_404(id)

    lead.status = request.form["status"]

    db.session.commit()

    return redirect("/leads")


# =========================
# DELETE LEAD
# =========================

@app.route(
    "/delete-lead/<int:id>"
)
def delete_lead(id):

    if not session.get("logged_in"):
        return redirect("/login")

    lead = Lead.query.get_or_404(id)

    db.session.delete(lead)

    db.session.commit()

    return redirect("/leads")


# =========================
# EDIT LEAD
# =========================

@app.route(
    "/edit-lead/<int:id>",
    methods=["GET", "POST"]
)
def edit_lead(id):

    if not session.get("logged_in"):
        return redirect("/login")

    lead = Lead.query.get_or_404(id)

    if request.method == "POST":

        lead.name = request.form["name"]

        lead.phone = request.form["phone"]

        lead.email = request.form["email"]

        lead.city = request.form["city"]

        lead.source = request.form["source"]

        lead.status = request.form["status"]

        lead.notes = request.form["notes"]

        db.session.commit()

        return redirect("/leads")

    return render_template(

        "edit_lead.html",

        lead=lead

    )


# =========================
# VIEW FOLLOW-UPS
# =========================

@app.route("/follow-ups")
def follow_ups():

    if not session.get("logged_in"):
        return redirect("/login")

    # Get all follow-ups
    all_follow_ups = FollowUp.query.order_by(
        FollowUp.due_date.asc()
    ).all()

    return render_template(
        "follow_ups.html",
        follow_ups=all_follow_ups
    )

    # =========================
    # CATEGORIES
    # =========================

    overdue_follow_ups = []

    today_follow_ups = []

    upcoming_follow_ups = []

    completed_follow_ups = []


    for follow_up in all_follow_ups:


        # COMPLETED

        if follow_up.completed:

            completed_follow_ups.append(
                follow_up
            )

            continue


        # OVERDUE

        if follow_up.due_date < now:

            overdue_follow_ups.append(
                follow_up
            )

            continue


        # TODAY

        if follow_up.due_date.date() == now.date():

            today_follow_ups.append(
                follow_up
            )

            continue


        # UPCOMING

        upcoming_follow_ups.append(
            follow_up
        )


    return render_template(

        "follow_ups.html",

        overdue_follow_ups=overdue_follow_ups,

        today_follow_ups=today_follow_ups,

        upcoming_follow_ups=upcoming_follow_ups,

        completed_follow_ups=completed_follow_ups

    )

# =========================
# ADD FOLLOW-UP
# =========================

@app.route(
    "/add-follow-up",
    methods=["GET", "POST"]
)
def add_follow_up():

    if not session.get("logged_in"):
        return redirect("/login")

    leads = Lead.query.order_by(
        Lead.name.asc()
    ).all()

    if request.method == "POST":

        due_date = datetime.strptime(
            request.form["due_date"],
            "%Y-%m-%dT%H:%M"
        )

        follow_up = FollowUp(

            lead_id=request.form["lead_id"],

            type=request.form["type"],

            title=request.form["title"],

            due_date=due_date,

            notes=request.form["notes"]

        )

        db.session.add(follow_up)

        db.session.commit()

        return redirect("/follow-ups")

    return render_template(
        "add_follow_up.html",
        leads=leads
    )

# =========================
# EDIT FOLLOW-UP
# =========================

@app.route(
    "/edit-follow-up/<int:id>",
    methods=["GET", "POST"]
)
def edit_follow_up(id):

    if not session.get("logged_in"):
        return redirect("/login")

    follow_up = FollowUp.query.get_or_404(id)

    leads = Lead.query.order_by(
        Lead.name.asc()
    ).all()

    if request.method == "POST":

        due_date = datetime.strptime(
            request.form["due_date"],
            "%Y-%m-%dT%H:%M"
        )

        follow_up.lead_id = request.form["lead_id"]

        follow_up.type = request.form["type"]

        follow_up.title = request.form["title"]

        follow_up.due_date = due_date

        follow_up.notes = request.form["notes"]

        db.session.commit()

        return redirect("/follow-ups")

    return render_template(
        "edit_follow_up.html",
        follow_up=follow_up,
        leads=leads
    )


# =========================
# COMPLETE FOLLOW-UP
# =========================

@app.route(
    "/complete-follow-up/<int:id>",
    methods=["POST"]
)
def complete_follow_up(id):

    if not session.get("logged_in"):
        return redirect("/login")

    follow_up = FollowUp.query.get_or_404(id)

    follow_up.completed = True

    db.session.commit()

    return redirect("/follow-ups")

# =========================
# REOPEN FOLLOW-UP
# =========================

@app.route(
    "/reopen-follow-up/<int:id>",
    methods=["POST"]
)
def reopen_follow_up(id):

    if not session.get("logged_in"):
        return redirect("/login")

    follow_up = FollowUp.query.get_or_404(id)

    follow_up.completed = False

    db.session.commit()

    return redirect("/follow-ups")


# =========================
# DELETE FOLLOW-UP
# =========================

@app.route(
    "/delete-follow-up/<int:id>"
)
def delete_follow_up(id):

    if not session.get("logged_in"):
        return redirect("/login")

    follow_up = FollowUp.query.get_or_404(id)

    db.session.delete(follow_up)

    db.session.commit()

    return redirect("/follow-ups")


# =========================
# DATABASE
# =========================

with app.app_context():

    db.create_all()

    # Add created_at to existing Lead database
    # if the column does not exist yet.

    inspector = inspect(
        db.engine
    )

    columns = [

        column["name"]

        for column in inspector.get_columns(
            "lead"
        )

    ]

    if "created_at" not in columns:

        with db.engine.connect() as connection:

            connection.execute(
                text(
                    "ALTER TABLE lead "
                    "ADD COLUMN created_at DATETIME"
                )
            )

            connection.commit()


# =========================
# RUN
# =========================

if __name__ == "__main__":

    app.run(
        debug=True
    )
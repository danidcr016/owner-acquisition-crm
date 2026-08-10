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


    # NEWEST FIRST

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
# DATABASE
# =========================

with app.app_context():

    db.create_all()


    # Add created_at to existing database
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
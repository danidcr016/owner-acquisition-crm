import os
import secrets

from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from flask import Flask, render_template, request, redirect, session
from datetime import datetime
from sqlalchemy import inspect, text
from werkzeug.security import generate_password_hash, check_password_hash

from discovery_scoring import calculate_score
from discovery import discovery_engine

# Load variables from a local .env file (ignored by git).
# In production (Render/Railway/etc.) these come from the
# platform's environment variable settings instead.
load_dotenv()

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = (
    os.environ.get("DATABASE_URL") or "sqlite:///database.db"
)

# Secret key used by Flask sessions. Must be set via
# environment variable — the app refuses to start without it.
secret_key = os.environ.get("SECRET_KEY")

if not secret_key:
    raise RuntimeError(
        "SECRET_KEY environment variable is not set. "
        "Create a .env file locally (see .env.example) or set "
        "SECRET_KEY in your hosting platform's environment settings. "
        "You can generate one with: python -c \"import secrets; "
        "print(secrets.token_hex(32))\""
    )

app.secret_key = secret_key

# Session expires when the browser is closed
app.config["SESSION_PERMANENT"] = False

# Only enable Flask's debug mode when explicitly requested
# via environment variable. NEVER True in production.
DEBUG_MODE = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

db = SQLAlchemy(app)


# =========================================================
# USER MODEL
# =========================================================

class User(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    username = db.Column(
        db.String(100),
        unique=True,
        nullable=False
    )

    password_hash = db.Column(
        db.String(255),
        nullable=False
    )

    role = db.Column(
        db.String(50),
        nullable=False,
        default="agent"
    )

    def __repr__(self):

        return f"<User {self.username} ({self.role})>"


# =========================================================
# LEAD MODEL
# =========================================================

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

    # Agent assigned to this lead
    assigned_to = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=True
    )

    assigned_user = db.relationship(
        "User",
        foreign_keys=[assigned_to],
        backref=db.backref(
            "assigned_leads",
            lazy=True
        )
    )

    def __repr__(self):

        return f"<Lead {self.name}>"


# =========================================================
# FOLLOW-UP MODEL
# =========================================================

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

# =========================================================
# DISCOVERY LEAD MODEL
# =========================================================

class DiscoveryLead(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    title = db.Column(
        db.String(200),
        nullable=False
    )

    description = db.Column(
        db.Text
    )

    city = db.Column(
        db.String(100)
    )

    source = db.Column(
        db.String(100)
    )

    url = db.Column(
        db.String(500)
    )

    score = db.Column(
        db.Integer,
        default=0
    )

    found_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    def __repr__(self):

        return f"<DiscoveryLead {self.title}>"



# =========================================================
# HELPER FUNCTIONS
# =========================================================

def current_user():

    user_id = session.get("user_id")

    if not user_id:
        return None

    return db.session.get(
        User,
        user_id
    )


def is_admin_or_developer():

    user = current_user()

    if not user:
        return False

    return user.role in [
        "admin",
        "developer"
    ]


def can_access_lead(lead):

    user = current_user()

    if not user:
        return False

    # Admin and developer can access everything
    if user.role in [
        "admin",
        "developer"
    ]:
        return True

    # Agents can only access their own leads
    return lead.assigned_to == user.id


def get_agents():

    return User.query.filter(
        User.role.in_([
            "agent",
            "admin"
        ])
    ).order_by(
        User.username.asc()
    ).all()

def create_discovery_lead(
    title,
    description,
    city,
    source,
    url,
    score=None
):

    # Check if this opportunity already exists
    existing_lead = DiscoveryLead.query.filter_by(
        url=url
    ).first()

    if existing_lead:

        return existing_lead

    # Calculate score if one was not provided
    if score is None:

        score = calculate_score(
            description or ""
        )

    discovery_lead = DiscoveryLead(

        title=title,

        description=description,

        city=city,

        source=source,

        url=url,

        score=score,

        found_at=datetime.utcnow()

    )

    db.session.add(
        discovery_lead
    )

    db.session.commit()

    return discovery_lead

# =========================================================
# LOGIN
# =========================================================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if request.method == "POST":

        username = request.form["username"].strip()

        password = request.form["password"]

        user = User.query.filter_by(
            username=username
        ).first()

        if user and check_password_hash(
            user.password_hash,
            password
        ):

            session.clear()

            session["logged_in"] = True
            session["user_id"] = user.id
            session["username"] = user.username
            session["role"] = user.role

            return redirect("/")

        return render_template(
            "login.html",
            error="Invalid username or password."
        )

    return render_template(
        "login.html"
    )


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect("/login")


# =========================================================
# DASHBOARD
# =========================================================

@app.route("/")
def home():

    if not session.get("logged_in"):
        return redirect("/login")

    user = current_user()

    if not user:

        session.clear()

        return redirect("/login")

    # Admin / Developer see everything
    if user.role in [
        "admin",
        "developer"
    ]:

        lead_query = Lead.query

    # Agent only sees assigned leads
    else:

        lead_query = Lead.query.filter_by(
            assigned_to=user.id
        )

    total_leads = lead_query.count()

    new_leads = lead_query.filter_by(
        status="NEW"
    ).count()

    contacted_leads = lead_query.filter_by(
        status="CONTACTED"
    ).count()

    interested_leads = lead_query.filter_by(
        status="INTERESTED"
    ).count()

    onboarding_leads = lead_query.filter_by(
        status="ONBOARDING"
    ).count()

    active_leads = lead_query.filter_by(
        status="ACTIVE"
    ).count()

    lost_leads = lead_query.filter_by(
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

        lost_leads=lost_leads,

        current_user=user
    )


# =========================================================
# ADD LEAD
# =========================================================

@app.route(
    "/add-lead",
    methods=["GET", "POST"]
)
def add_lead():

    if not session.get("logged_in"):
        return redirect("/login")

    user = current_user()

    if not user:

        session.clear()

        return redirect("/login")

    if request.method == "POST":

        # Admin / developer can assign a lead
        if user.role in [
            "admin",
            "developer"
        ]:

            assigned_to = request.form.get(
                "assigned_to"
            )

            if assigned_to:

                assigned_user = User.query.filter(
                    User.id == int(assigned_to),
                    User.role.in_(["agent", "admin"])
                ).first()

                if not assigned_user:

                    return "Invalid agent", 400

                assigned_to = assigned_user.id

            else:

                assigned_to = None

        # Agent automatically gets the lead assigned to themselves
        else:

            assigned_to = user.id

        lead = Lead(

            name=request.form["name"],

            phone=request.form["phone"],

            email=request.form["email"],

            city=request.form["city"],

            source=request.form["source"],

            status=request.form["status"],

            notes=request.form["notes"],

            assigned_to=assigned_to,

            created_at=datetime.utcnow()

        )

        db.session.add(lead)

        db.session.commit()

        return render_template(
            "success.html"
        )

    # Only admin/developer need agent selector
    if user.role in [
        "admin",
        "developer"
    ]:

        agents = get_agents()

    else:

        agents = []

    return render_template(

        "add_lead.html",

        agents=agents,

        current_user=user
    )


# =========================================================
# VIEW LEADS
# SEARCH + FILTER
# =========================================================

@app.route("/leads")
def leads():

    if not session.get("logged_in"):
        return redirect("/login")

    user = current_user()

    if not user:

        session.clear()

        return redirect("/login")

    search = request.args.get(
        "search",
        ""
    ).strip()

    status_filter = request.args.get(
        "status",
        ""
    ).strip()

    # =====================================================
    # LEADS FOR SEARCH SUGGESTIONS
    # =====================================================

    if user.role in [
        "admin",
        "developer"
    ]:

        suggestion_query = Lead.query

    else:

        suggestion_query = Lead.query.filter_by(
            assigned_to=user.id
        )

    lead_suggestions = suggestion_query.order_by(
        Lead.name.asc()
    ).all()

    # =====================================================
    # LEADS VISIBLE IN TABLE
    # =====================================================

    # Admin / Developer
    if user.role in [
        "admin",
        "developer"
    ]:

        query = Lead.query

    # Agent
    else:

        query = Lead.query.filter_by(
            assigned_to=user.id
        )

    # =====================================================
    # SEARCH BY NAME, PHONE OR CITY
    # =====================================================

    if search:

        search_pattern = f"%{search}%"

        query = query.filter(
            db.or_(
                Lead.name.ilike(search_pattern),
                Lead.phone.ilike(search_pattern),
                Lead.city.ilike(search_pattern)
            )
        )

    # =====================================================
    # FILTER BY STATUS
    # =====================================================

    if status_filter:

        query = query.filter(
            Lead.status == status_filter
        )

    # =====================================================
    # ORDER BY ID
    # =====================================================

    query = query.order_by(
        Lead.id.asc()
    )

    all_leads = query.all()

    # =====================================================
    # AGENTS FOR ASSIGNMENT DROPDOWN
    # =====================================================

    if user.role in [
        "admin",
        "developer"
    ]:

        agents = get_agents()

    else:

        agents = []

    return render_template(

        "leads.html",

        leads=all_leads,

        search=search,

        status_filter=status_filter,

        current_user=user,

        agents=agents,

        lead_suggestions=lead_suggestions
    )


# =========================================================
# UPDATE STATUS
# =========================================================

@app.route(
    "/update-status/<int:id>",
    methods=["POST"]
)
def update_status(id):

    if not session.get("logged_in"):
        return redirect("/login")

    lead = Lead.query.get_or_404(id)

    if not can_access_lead(lead):

        return "Access denied", 403

    lead.status = request.form["status"]

    db.session.commit()

    return redirect("/leads")


# =========================================================
# DELETE LEAD
# ADMIN / DEVELOPER ONLY
# =========================================================

@app.route(
    "/delete-lead/<int:id>"
)
def delete_lead(id):

    if not session.get("logged_in"):
        return redirect("/login")

    if not is_admin_or_developer():

        return "Access denied", 403

    lead = Lead.query.get_or_404(id)

    db.session.delete(lead)

    db.session.commit()

    return redirect("/leads")


# =========================================================
# EDIT LEAD
# =========================================================

@app.route(
    "/edit-lead/<int:id>",
    methods=["GET", "POST"]
)
def edit_lead(id):

    if not session.get("logged_in"):
        return redirect("/login")

    user = current_user()

    if not user:

        session.clear()

        return redirect("/login")

    lead = Lead.query.get_or_404(id)

    # Agent can only edit assigned leads
    if not can_access_lead(lead):

        return "Access denied", 403

    if request.method == "POST":

        lead.name = request.form["name"]

        lead.phone = request.form["phone"]

        lead.email = request.form["email"]

        lead.city = request.form["city"]

        lead.source = request.form["source"]

        lead.status = request.form["status"]

        lead.notes = request.form["notes"]

        # Admin / developer can change assignment
        if user.role in [
            "admin",
            "developer"
        ]:

            assigned_to = request.form.get(
                "assigned_to"
            )

            if assigned_to:

                assigned_user = User.query.filter(
                    User.id == int(assigned_to),
                    User.role.in_(["agent", "admin"])
                ).first()

                if not assigned_user:

                    return "Invalid agent", 400

                lead.assigned_to = assigned_user.id

            else:

                lead.assigned_to = None

        db.session.commit()

        return redirect("/leads")

    # Agents available for admin/developer
    if user.role in [
        "admin",
        "developer"
    ]:

        agents = get_agents()

    else:

        agents = []

    return render_template(

        "edit_lead.html",

        lead=lead,

        agents=agents,

        current_user=user
    )


# =========================================================
# VIEW FOLLOW-UPS
# =========================================================

@app.route("/follow-ups")
def follow_ups():

    if not session.get("logged_in"):
        return redirect("/login")

    user = current_user()

    if not user:

        session.clear()

        return redirect("/login")

    # Admin / Developer see all follow-ups
    if user.role in [
        "admin",
        "developer"
    ]:

        all_follow_ups = FollowUp.query.order_by(
            FollowUp.due_date.asc()
        ).all()

    # Agent sees only follow-ups belonging
    # to their assigned leads
    else:

        all_follow_ups = FollowUp.query.join(
            Lead
        ).filter(
            Lead.assigned_to == user.id
        ).order_by(
            FollowUp.due_date.asc()
        ).all()

    return render_template(

        "follow_ups.html",

        follow_ups=all_follow_ups,

        current_user=user
    )


# =========================================================
# ADD FOLLOW-UP
# =========================================================

@app.route(
    "/add-follow-up",
    methods=["GET", "POST"]
)
def add_follow_up():

    if not session.get("logged_in"):
        return redirect("/login")

    user = current_user()

    if not user:

        session.clear()

        return redirect("/login")

    # Admin / Developer can see all leads
    if user.role in [
        "admin",
        "developer"
    ]:

        leads = Lead.query.order_by(
            Lead.name.asc()
        ).all()

    # Agent only sees their leads
    else:

        leads = Lead.query.filter_by(
            assigned_to=user.id
        ).order_by(
            Lead.name.asc()
        ).all()

    if request.method == "POST":

        lead_id = int(
            request.form["lead_id"]
        )

        lead = Lead.query.get_or_404(
            lead_id
        )

        # User must have access to lead
        if not can_access_lead(lead):

            return "Access denied", 403

        due_date = datetime.strptime(

            request.form["due_date"],

            "%Y-%m-%dT%H:%M"

        )

        follow_up = FollowUp(

            lead_id=lead_id,

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

        leads=leads,

        current_user=user
    )


# =========================================================
# EDIT FOLLOW-UP
# =========================================================

@app.route(
    "/edit-follow-up/<int:id>",
    methods=["GET", "POST"]
)
def edit_follow_up(id):

    if not session.get("logged_in"):
        return redirect("/login")

    user = current_user()

    if not user:

        session.clear()

        return redirect("/login")

    follow_up = FollowUp.query.get_or_404(id)

    # Agent can only access follow-ups
    # belonging to their own leads
    if not can_access_lead(
        follow_up.lead
    ):

        return "Access denied", 403

    # Admin / Developer see all leads
    if user.role in [
        "admin",
        "developer"
    ]:

        leads = Lead.query.order_by(
            Lead.name.asc()
        ).all()

    # Agent only sees their own leads
    else:

        leads = Lead.query.filter_by(
            assigned_to=user.id
        ).order_by(
            Lead.name.asc()
        ).all()

    if request.method == "POST":

        lead_id = int(
            request.form["lead_id"]
        )

        selected_lead = Lead.query.get_or_404(
            lead_id
        )

        # Cannot move follow-up
        # to an inaccessible lead
        if not can_access_lead(
            selected_lead
        ):

            return "Access denied", 403

        due_date = datetime.strptime(

            request.form["due_date"],

            "%Y-%m-%dT%H:%M"

        )

        follow_up.lead_id = lead_id

        follow_up.type = request.form["type"]

        follow_up.title = request.form["title"]

        follow_up.due_date = due_date

        follow_up.notes = request.form["notes"]

        db.session.commit()

        return redirect("/follow-ups")

    return render_template(

        "edit_follow_up.html",

        follow_up=follow_up,

        leads=leads,

        current_user=user
    )


# =========================================================
# COMPLETE FOLLOW-UP
# =========================================================

@app.route(
    "/complete-follow-up/<int:id>",
    methods=["POST"]
)
def complete_follow_up(id):

    if not session.get("logged_in"):
        return redirect("/login")

    follow_up = FollowUp.query.get_or_404(
        id
    )

    if not can_access_lead(
        follow_up.lead
    ):

        return "Access denied", 403

    follow_up.completed = True

    db.session.commit()

    return redirect("/follow-ups")


# =========================================================
# REOPEN FOLLOW-UP
# =========================================================

@app.route(
    "/reopen-follow-up/<int:id>",
    methods=["POST"]
)
def reopen_follow_up(id):

    if not session.get("logged_in"):
        return redirect("/login")

    follow_up = FollowUp.query.get_or_404(
        id
    )

    if not can_access_lead(
        follow_up.lead
    ):

        return "Access denied", 403

    follow_up.completed = False

    db.session.commit()

    return redirect("/follow-ups")


# =========================================================
# DELETE FOLLOW-UP
# ADMIN / DEVELOPER ONLY
# =========================================================

@app.route(
    "/delete-follow-up/<int:id>"
)
def delete_follow_up(id):

    if not session.get("logged_in"):
        return redirect("/login")

    if not is_admin_or_developer():

        return "Access denied", 403

    follow_up = FollowUp.query.get_or_404(
        id
    )

    db.session.delete(follow_up)

    db.session.commit()

    return redirect("/follow-ups")


# =========================================================
# DISCOVERY
# =========================================================

@app.route("/discovery")
def discovery():

    if not session.get("logged_in"):
        return redirect("/login")

    user = current_user()

    if not user:

        session.clear()

        return redirect("/login")

    if user.role == "agent":

        return redirect("/")

    opportunities = DiscoveryLead.query.order_by(
        DiscoveryLead.score.desc()
    ).all()

    return render_template(
        "discovery.html",
        opportunities=opportunities,
        current_user=user
    )

# =========================================================
# DISCOVERY - RUN CRAIGSLIST SCAN (TEMPORARY, FOR TESTING)
# =========================================================

@app.route(
    "/discovery/run-craigslist",
    methods=["POST"]
)
def run_craigslist_scan():

    if not session.get("logged_in"):
        return redirect("/login")

    user = current_user()

    if not user or user.role == "agent":

        return "Access denied", 403

    ads = discovery_engine.scan()

    discovery_engine.process_ads(
        ads,
        create_discovery_lead
    )

    return redirect("/discovery")



# =========================================================
# DATABASE SETUP + MIGRATIONS
# =========================================================

with app.app_context():

    # Create tables that don't exist
    db.create_all()

    inspector = inspect(
        db.engine
    )

    # =====================================================
    # LEAD TABLE MIGRATION
    # =====================================================

    lead_columns = [

        column["name"]

        for column in inspector.get_columns(
            "lead"
        )

    ]

    # Add created_at if it doesn't exist
    if "created_at" not in lead_columns:

        with db.engine.connect() as connection:

            connection.execute(
                text(
                    "ALTER TABLE lead "
                    "ADD COLUMN created_at DATETIME"
                )
            )

            connection.commit()

    # Add assigned_to if it doesn't exist
    if "assigned_to" not in lead_columns:

        with db.engine.connect() as connection:

            connection.execute(
                text(
                    "ALTER TABLE lead "
                    "ADD COLUMN assigned_to INTEGER"
                )
            )

            connection.commit()

    # =====================================================
    # CREATE DEFAULT USERS
    # =====================================================
    # Passwords come from environment variables, never from
    # source code. If a variable isn't set, that user is
    # skipped (with a warning) instead of falling back to a
    # guessable default. If a user account doesn't exist yet
    # and no password was provided for it, a random one-time
    # password is generated and printed once so the app can
    # still start — log in with it and change it right away.

    users_to_create = [
        {
            "username": "jeremy",
            "env_var": "ADMIN_PASSWORD",
            "role": "admin"
        },
        {
            "username": "daniel",
            "env_var": "DEVELOPER_PASSWORD",
            "role": "developer"
        },
        {
            "username": "agent",
            "env_var": "AGENT_PASSWORD",
            "role": "agent"
        }
    ]

    for user_data in users_to_create:

        existing_user = User.query.filter_by(
            username=user_data["username"]
        ).first()

        if existing_user:
            continue

        password = os.environ.get(user_data["env_var"])

        if not password:
            password = secrets.token_urlsafe(12)
            print(
                f"[setup] {user_data['env_var']} not set — "
                f"generated a one-time password for "
                f"'{user_data['username']}': {password}\n"
                f"[setup] Log in and change it, or set "
                f"{user_data['env_var']} and recreate the user."
            )

        new_user = User(
            username=user_data["username"],
            password_hash=generate_password_hash(password),
            role=user_data["role"]
        )

        db.session.add(new_user)

    db.session.commit()


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    app.run(
        debug=DEBUG_MODE
    )

    
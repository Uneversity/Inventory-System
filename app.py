import math
import copy
import csv
import os
import json
import time
import statistics
from os import path

from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, json, render_template, render_template_string, request, redirect, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

app = Flask(__name__)

#API Key set to an environment variable for security.
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-key-change-me")

#SQL Config
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///local_inventory.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Heroku provides the database URL in the format "postgres://", but SQLAlchemy expects "postgresql://". This code ensures compatibility by replacing the prefix if necessary.
if app.config["SQLALCHEMY_DATABASE_URI"].startswith("postgres://"):
    app.config["SQLALCHEMY_DATABASE_URI"] = app.config["SQLALCHEMY_DATABASE_URI"].replace("postgres://", "postgresql://", 1)

db = SQLAlchemy(app)

credentials = {}
user_data = {}

app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='user')
    low_item_threshold = db.Column(db.Integer, default=5)
    
    # Relationships
    inventory_items = db.relationship('InventoryItem', backref='user', lazy=True, cascade='all, delete-orphan')
    categories = db.relationship('Category', backref='user', lazy=True, cascade='all, delete-orphan')
    activity_logs = db.relationship('ActivityLog', backref='user', lazy=True, cascade='all, delete-orphan')

class InventoryItem(db.Model):
    __tablename__ = 'inventory_items'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    quantity = db.Column(db.Integer, default=0)
    category = db.Column(db.String(100), default='Uncategorized')
    last_modified = db.Column(db.String(50))
    
    # Each user can't have duplicate item names
    __table_args__ = (db.UniqueConstraint('user_id', 'name', name='_user_item_uc'),)

class Category(db.Model):
    __tablename__ = 'categories'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    color = db.Column(db.String(7), default='#000000')  # Hex color
    
    __table_args__ = (db.UniqueConstraint('user_id', 'name', name='_user_category_uc'),)

class ActivityLog(db.Model):
    __tablename__ = 'activity_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    timestamp = db.Column(db.String(50), nullable=False)
    action = db.Column(db.String(100), nullable=False)
    item = db.Column(db.String(200), nullable=False)
    detail = db.Column(db.Text)

@app.route("/create_tables")
def create_tables():
    """One-time setup: creates all database tables"""
    db.create_all()
    return "Database tables created! You can delete this route now."

@app.route("/debug_db")
def debug_db():
    db_uri = app.config['SQLALCHEMY_DATABASE_URI']
    return f"<h1>Database URI:</h1><p>{db_uri}</p>"

# ---------------------------------------------------------------------------
# DATABASE INTERACTION FUNCTIONS (LOAD/SAVE FOR INVENTORY, LOGS, USERS, CATEGORIES)
# ---------------------------------------------------------------------------

def make_sure_data_structure_exists_for_user(username):

    if username not in user_data:
        user_data[username] = {
            "inventory_dictionary": {},
            "low_item_threshold": 5,
            "history": [],
            "future": [],
            "pending_changes": {},
            "activity_log": [],
            "categories_and_colors": {
                "Uncategorized": "#FFFFFF",
            }
        }

def save():
    username = session.get("username")
    user = User.query.filter_by(username=username).first()
    
    # Delete old inventory items for this user
    InventoryItem.query.filter_by(user_id=user.id).delete()
    
    # Insert current inventory
    inventory = user_data[username]["inventory_dictionary"]
    for item_name, data in inventory.items():
        new_item = InventoryItem(
            user_id=user.id,
            name=item_name,
            quantity=data["quantity"],
            category=data["category"],
            last_modified=data.get("last_modified", "Never")
        )
        db.session.add(new_item)
    
    db.session.commit()

def load():
    username = session.get("username")
    make_sure_data_structure_exists_for_user(username)
    
    user = User.query.filter_by(username=username).first()
    if not user:
        user_data[username]["inventory_dictionary"] = {}
        return
    
    items = InventoryItem.query.filter_by(user_id=user.id).all()
    
    user_data[username]["inventory_dictionary"] = {
        item.name: {
            "quantity": item.quantity,
            "category": item.category,
            "last_modified": item.last_modified or "Never"
        }
        for item in items
    }

def save_log():
    username = session.get("username")
    user = User.query.filter_by(username=username).first()
    
    if not user:
        return
    
    # Get the most recent log entry (the one we just added)
    logs = user_data[username]["activity_log"]
    if not logs:
        return
    
    # Add only the newest log entry to database
    latest_log = logs[-1]
    new_log = ActivityLog(
        user_id=user.id,
        timestamp=latest_log["timestamp"],
        action=latest_log["action"],
        item=latest_log["item"],
        detail=latest_log["detail"]
    )
    db.session.add(new_log)
    db.session.commit()

def load_log():
    username = session.get("username")
    make_sure_data_structure_exists_for_user(username)
    
    user = User.query.filter_by(username=username).first()
    if not user:
        user_data[username]["activity_log"] = []
        return
    
    logs = ActivityLog.query.filter_by(user_id=user.id).order_by(ActivityLog.id.desc()).all()
    
    user_data[username]["activity_log"] = [
        {
            "timestamp": log.timestamp,
            "action": log.action,
            "item": log.item,
            "detail": log.detail
        }
        for log in logs
    ]

def save_users():
    """Save users to database (no longer needed since register saves directly, but kept for consistency)"""
    # This function is mostly obsolete now because:
    # - register_user() saves directly to database
    # - login doesn't modify user data
    # But we can keep it in case we ever need to bulk-update users
    
    for username, data in credentials.items():
        # Check if user exists in database
        user = User.query.filter_by(username=username).first()
        
        if user:
            # Update existing user
            user.password_hash = data["password"]
            user.role = data.get("role", "user")
        else:
            # Create new user
            new_user = User(
                username=username,
                password_hash=data["password"],
                role=data.get("role", "user"),
                low_item_threshold=5
            )
            db.session.add(new_user)
    
    db.session.commit()

def load_users():
    global credentials
    
    users = User.query.all()
    
    credentials = {
        user.username: {
            "password": user.password_hash,
            "role": user.role
        }
        for user in users
    }
    
    # Create default admin if database is empty
    if not credentials:
        default_password = os.getenv("ADMIN_PASSWORD", "admin123")
        admin_user = User(
            username="TesterUser",
            password_hash=generate_password_hash(default_password),
            role="admin",
            low_item_threshold=5
        )
        db.session.add(admin_user)
        db.session.commit()
        
        credentials["TesterUser"] = {
            "password": admin_user.password_hash,
            "role": "admin"
        }

def save_categories_and_colors():
    username = session.get("username")
    user = User.query.filter_by(username=username).first()
    
    if not user:
        return
    
    # Delete old categories
    Category.query.filter_by(user_id=user.id).delete()
    
    # Insert current categories
    categories = user_data[username]["categories_and_colors"]
    for cat_name, color in categories.items():
        new_cat = Category(
            user_id=user.id,
            name=cat_name,
            color=color
        )
        db.session.add(new_cat)
    
    db.session.commit()

def load_categories_and_colors():
    username = session.get("username")
    make_sure_data_structure_exists_for_user(username)
    
    user = User.query.filter_by(username=username).first()
    if not user:
        user_data[username]["categories_and_colors"] = {"Uncategorized": "#FFFFFF"}
        return
    
    categories = Category.query.filter_by(user_id=user.id).all()
    
    user_data[username]["categories_and_colors"] = {
        cat.name: cat.color
        for cat in categories
    }
    
    # Ensure Uncategorized exists
    if "Uncategorized" not in user_data[username]["categories_and_colors"]:
        user_data[username]["categories_and_colors"]["Uncategorized"] = "#FFFFFF"

# ---------------------------------------------------------------------------
# UTILITY FUNCTIONS
# ---------------------------------------------------------------------------

def get_current_user():

    return session.get("username"), session.get("role")

def get_user_data(key):

    username = session.get("username")

    make_sure_data_structure_exists_for_user(username)

    return user_data[username].get(key)

def set_user_data(key, value):

    username = session.get("username")
    user_data[username][key] = value

def snapshot():

    #username = session.get("username")
    #print(f"DEBUG: username={username}, user_data keys={list(user_data.keys())}")
    
    history = get_user_data("history")
    #print(f"DEBUG: history exists? {history is not None}, length={len(history) if history else 0}")

    inventory_dictionary = get_user_data("inventory_dictionary") # Get the inventory dictionary for the current user
    history.append(copy.deepcopy(inventory_dictionary))  # Save a deep copy of the current state to history


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S") 

def time_stamper(item):
    inventory_dictionary = get_user_data("inventory_dictionary") # Get the inventory dictionary for the current user
    if item in inventory_dictionary:
        inventory_dictionary[item]["last_modified"] = now()

def protected_route(func):
    def wrapper(*args, **kwargs):
        username = get_current_user()
        if not username:
            return redirect("/login")
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper

def log_activity(item, action, detail):
    activity_log = get_user_data("activity_log") # Get the activity log for the current user
    activity_log.append({
        "timestamp": now(),
        "action": action,
        "item": item,
        "detail": detail
    })
    save_log()   

def auto_logout():
    if not session:
        return redirect("/login") 

# ---------------------------------------------------------------------------
# LOGIN PAGE/LOGOUT ROUTE/REGISTRATION ROUTE
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():

    to_be_registered = request.form.get("register") == "true" if request.method == "POST" else False #
    if to_be_registered:
        return redirect("/register")

    error = None
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        user = credentials.get(username)
        if user and check_password_hash(user["password"], password):
            session["username"] = username # Store the username in the session to keep the user logged in across requests
            session["role"] = user.get("role", "user")
            return redirect("/")
        error = "Invalid username or password"
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/register", methods=["GET", "POST"])
def register_user():

    to_not_be_registered = request.form.get("register") == "false" if request.method == "POST" else False
    if to_not_be_registered:
        return redirect("/login")

    if request.method == "POST": #If the form is submitted
        username = request.form["username"]
        password = request.form["password"]

        #Validation checks
        if username in credentials: #Check if username already exists
            return render_template("register.html", error="Username already exists") #return error message if username is taken
        if not username or not password: #Check if username or password form was submitted empty
            return render_template("register.html", error="Username and password are required")
        

        # Create new user in database
        new_user = User(
            username=username,
            password_hash=generate_password_hash(password),
            role="user",
            low_item_threshold=5
        )

        db.session.add(new_user)
        db.session.commit()

        # Update in-memory credentials
        credentials[username] = {
            "password": new_user.password_hash,
            "role": "user"
        }

        #initiate user data structure for new user in memory (also created when loading inventory, but this ensures it's there from the start)
        user_data[username] = {
            "inventory_dictionary": {},
            "low_item_threshold": 5,
            "history": [],
            "future": [],
            "pending_changes": {},
            "activity_log": [],
            "categories_and_colors": {
                "Uncategorized": "#FFFFFF",
            }
        }

        #auto-login after registration
        session["username"] = username
        session["role"] = "user"

        return redirect("/")
    return render_template("register.html")

# ---------------------------------------------------------------------------
# MAIN ROUTES (HOME AND LOG PAGE)
# ---------------------------------------------------------------------------

@app.route("/")
@protected_route
def home():

    username = session.get("username")

    if not session.get("username"):
        return redirect("/login")
    
    username, role = get_current_user()

    load()  # Load the inventory for the current user
    load_categories_and_colors() # Load the categories and colors for the current user
    load_log() # Load the activity log for the current user

    inventory_dictionary = get_user_data("inventory_dictionary") # Get the inventory dictionary for the current user
    low_item_threshold = get_user_data("low_item_threshold") # Get the low item threshold for the current user
    pending_changes = get_user_data("pending_changes") # Get the pending changes for the current user
    categories_and_colors = get_user_data("categories_and_colors") # Get the categories and colors for the current user

    #Paggination Stuff
    items_per_page = 150
    #Current page is determined by the "page" query parameter in the URL, defaulting to 1 if not provided or invalid
    current_page = request.args.get("page", 1, type=int)

    # Get all items as a list of tuples (item, data)
    all_items = list(inventory_dictionary.items())
    total_items = len(all_items)
    total_pages = math.ceil(total_items / items_per_page)

    start_index = (current_page - 1) * items_per_page
    end_index = start_index + items_per_page
    paginated_items = all_items[start_index:end_index] # Get only the items for the current page

    #Con

    rows = ""
    qty_display = ""
    action_buttons = ""
    category_html_edit = ""

    # Get unique categories for filter dropdown using set sytax
    unique_category_list = set(cat_name for cat_name in categories_and_colors.keys())

    for item, data in paginated_items:


        amount = data["quantity"] # Get the quantity of the item from the inventory dictionary
        category = data["category"] # Get the category of the item from the inventory dictionary
        warning = warning_alert(amount) # Get the appropriate warning emoji based on the quantity and low item threshold
        category_html = category_html_return(category) # Get the HTML for displaying the category badge with the correct color based on the category

        is_pending = pending_changes and pending_changes["items"] == item #boolen to check if there are pending changes for this item
        pending_qty = amount + pending_changes["change"] if is_pending else amount #int value to show the pending quantity if there are pending changes, otherwise just show the normal amount

        # Display pending quantity in orange with a "pending" label if there are staged changes, otherwise show the normal quantity with edit options
        if is_pending:
            qty_display = f'<span style="color: orange; font-weight: bold;">{pending_qty} (pending {pending_changes["change"]:+})</span>'
            #qty_display = f'<span style="color: orange; font-weight: bold;">{pending_qty} (pending)</span>'
        else:
            qty_display = f""" 

                <!-- CLICK QUANTITY TO EDIT INLINE -->
                <!-- Clicking the quantity shows an edit box in place of this text -->
                <span class="editable-qty" onclick="startEditQty(this, '{item}', {amount})">
                    {amount}{warning}
                </span>

                <!-- Hidden quantity edit form with ✓ and ✗, revealed by startEditQty() -->
                <form class="edit-qty-form" style="display:none;" method="POST" action="/edit_qty">
                    <input type="hidden" name="item" value="{item}">
                    <input type="number" name="quantity" value="{amount}" style="width:60px; background-color: transparent; border: 1px solid #555; border-radius: 6px; color: white; padding: 5px 7px; margin-left: -6px;" min="0">
                    <button type="submit" class="btn">✓</button>
                    <button type="button" onclick="cancelEdit(this)" class="btn">✗</button>
                </form>

            """

        # If there are pending changes for this item, show only Confirm/Cancel buttons; otherwise show the normal +1/-1 and Remove options
        if is_pending:
            action_buttons = f""" 
                <!-- FORM FOR QUICK +1 -->
                <form style="display:inline;" method="POST" action="/stage_increase">
                    <input type="hidden" name="item" value="{item}">
                    <button type="submit" class="btn">+</button>
                </form>

                <!-- FORM FOR QUICK -1 -->
                <form style="display:inline;" method="POST" action="/stage_decrease">
                    <input type="hidden" name="item" value="{item}">
                    <button type="submit" class="btn"
                    {"disabled" if pending_qty == 0 else ""}
                    >-</button>
                </form>

                <!-- FORM TO CONFIRM PENDING CHANGES -->
                <form style="display:inline;" method="POST" action="/confirm">
                    <input type="hidden" name="item" value="{item}">
                    <button type="submit" class="btn">✓ Confirm</button>
                </form>

                <!-- FORM TO CANCEL PENDING CHANGES -->
                <form style="display:inline;" method="POST" action="/cancel">
                    <input type="hidden" name="item" value="{item}">
                    <button type="submit" class="btn">✗ Cancel</button> 
                </form>
                """
        else:
            action_buttons = f""" 
                <!-- FORM FOR QUICK +1 -->
                <form style="display:inline;" method="POST" action="/stage_increase">
                    <input type="hidden" name="item" value="{item}">
                    <button type="submit" class="btn">+</button>
                </form>

                <!-- FORM FOR QUICK -1 -->
                <form style="display:inline;" method="POST" action="/stage_decrease">
                    <input type="hidden" name="item" value="{item}">
                    <button type="submit"
                    {"disabled" if amount == 0 else ""}
                    {'onclick="return confirm(\'Quantity is already 0. Do you want to remove the item instead?\')"' if amount == 0 else ""}
                    class="btn">-</button>
                </form>

                <!-- FORM TO REMOVE ITEM -->
                <form style="display:inline; " method="POST" action="/remove">
                    <input type="hidden" name="item" value="{item}">
                    <button type="submit"
                    {'onclick="return confirm(\'Are you sure you want to remove?\')"'}
                    class="btn btn-remove">Remove</button>
                </form>
                """



        rows += f"""
        <tr>

            <td>
                <!-- CLICK NAME OF ITEM TO EDIT INLINE -->
                <!-- Clicking the item name shows an edit box in place of this text -->
                <span class="editable-name" onclick="startEditName(this, '{item}')">
                    {item}
                </span>
                
                <!-- This edit form with ✓ and ✗ is hidden by default; startEditName() reveals it -->
                <form class="edit-name-form" style="display:none;" method="POST" action="/edit_name"> 
                    <input type="hidden" name="old_name" value="{item}">
                    <input type="text" name="new_name" value="{item}" style="width:100px; background-color: transparent; border: 1px solid #555; border-radius: 6px; color: white; padding: 5px 7px; margin-left: 5px;">
                    <button type="submit" class="btn">✓</button>
                    <button type="button" onclick="cancelEdit(this)" class="btn">✗</button>
                </form>
            </td>
            
            <td>{qty_display}</td>

            <td>
                <!-- CLICK CATEGORY TO EDIT INLINE -->
                <!-- Clicking the category shows an edit box in place of this text -->
                <span class="editable-category" onclick="startEditCategory(this, '{item}')">
                    {category_html} 
                </span>

                <!-- Hidden category edit form with ✓ and ✗, revealed by startEditCategory() -->
                <form class="edit-category-form" style="display:none;" method="POST" action="/edit_category">
                    <input type="hidden" name="item" value="{item}">
                    <select name="category"
                        style="background-color: transparent; border: 1px solid #555; border-radius: 6px; color: white; padding: 5px 7px; margin-left: 5px;"
                        onchange="updateDropdownColor(this)"
                        onfocus="updateDropdownColor(this)">
                            {''.join(f'<option value="{cat}" {"selected" if cat == category else ""}>{cat}</option>' for cat in unique_category_list)}
                    </select>
                    <button type="submit" class="btn">✓</button>
                    <button type="button" onclick="cancelEdit(this)" class="btn">✗</button>
                </form>
            </td>

            <td>{data.get("last_modified", "Never")}</td>

            <td>{action_buttons}</td>

        </tr> 
        """

    pagination_html = ""

    pagination_html = f'<div style="margin-top: 20px; text-align: center;">'
    pagination_html += f'<p>Showing {start_index + 1}-{min(end_index, total_items)} of {total_items} items</p>'

    if total_pages > 1:
        pagination_html += '<div style="margin-top: 20px; text-align: center;">'

        #Previous button, only shown if not on the first page
        if current_page > 1:
            pagination_html += f'<a href="/?page={current_page - 1}"><button class="btn">Previous</button></a>'
        for page_number in range(1, total_pages + 1):
            if page_number == current_page:
                pagination_html += f'<span style="margin: 0 5px; font-weight: bold;">{page_number}</span>'
            else:
                pagination_html += f'<a href="/?page={page_number}"><button class="btn">{page_number}</button></a>'
        #Next button, only shown if not on the last page
        if current_page < total_pages:
            pagination_html += f'<a href="/?page={current_page + 1}"><button class="btn">Next</button></a>'
    
        pagination_html += '</div>' #Close pagination container div

    return render_template("home.html", rows=rows, username=username, role=role, low_item_threshold=low_item_threshold, categories=sorted(unique_category_list), cats_and_colors=categories_and_colors, pagination_html=pagination_html)

@app.route("/log")
@protected_route
def log_page():

    username = session.get("username")
    rows = ""

    for entry in reversed(user_data[username]["activity_log"]):  # Show most recent first
        rows += f"""
        <tr>
            <td class="timestamp">{entry['timestamp']}</td>
            <td><strong>{entry['item']}</strong></td>
            <td>{entry['action']}</td>
            <td style="color:aeaeae;">{entry['detail']}</td>
        </tr>
        """
    return render_template("logs.html", rows=rows)

@app.route("/categories")
@protected_route
def category_edit_page():

    username = session.get("username")
    rows = ""
    for category_name, hex in user_data[username]["categories_and_colors"].items():
        rows += f"""
        <tr>
            <td>{category_name}</td>
            <td><span style="color: #292929; background-color: {hex}; padding: 3.5px 7px; border-radius: 999px;">{hex}</span></td>
            <td>
                <form method="POST" action="/change_category_badge_color">
                    <input type="hidden" name="category_name" value="{category_name}"> <!-- Hidden field to identify which category is being edited -->
                    <input type="color" name="color" value="{hex}"> <!-- Color picker input to select new color -->
                    <button type="submit" class="btn">Change Color</button> <!-- Submit button to save the new color -->
                </form>
            </td>
        </tr>
        """


    
    return f"""

        <table border="1" cellpadding="8">
            <thead>
                <tr>
                    <th>Category</th>
                    <th>Current Color</th>
                    <th>Edit</th>
                </tr>
            </thead>
            <tbody>
            {rows}
            </tbody>
        </table>

        <form method="POST" action="/add_new_category" style="display:contents;">
            <div class="field">
                <label>Category Name</label>
                <input name="category" placeholder="e.g. Vegetables, Fruits, etc.">
            </div>
            <button type="submit" class="btn-add">Add Category</button> 
        </form>

    <a href='/'><button class='btn-ghost'>Back to Inventory</button></a>"""

# ---------------------------------------------------------------------------
# INVENTORY LOGIC FUNCTIONS (ADD, EDIT, REMOVE, THRESHOLD, UNDO/REDO)
# ---------------------------------------------------------------------------

def preliminaries():

    future = get_user_data("future") # Get the future list for the current user

    snapshot()          # save current state first
    future.clear()      # new change wipes redo history

    item = request.form["item"].strip().title() if "item" in request.form else None

    if item:
        return item 
    elif not item:
        return None # If the item name is empty, return None to prevent adding an item with no name

@app.route("/edit_name", methods=["POST"])
@protected_route
def edit_name():

    preliminaries()

    old_name = request.form["old_name"].strip().title()
    new_name = request.form["new_name"].strip().title()

    # Only update if both names are valid and the item actually exists
    inventory_dictionary = get_user_data("inventory_dictionary") # Get the inventory dictionary for the current user
    if old_name in inventory_dictionary and new_name:
        if new_name != old_name:
            quantity = inventory_dictionary[old_name]["quantity"]
            category = inventory_dictionary[old_name]["category"]
            inventory_dictionary.pop(old_name)  # Remove the old entry
            # If the new name already exists, merge the quantities
            inventory_dictionary[new_name] = {"quantity": inventory_dictionary.get(new_name, {}).get("quantity", 0) + quantity, "category": category}
            time_stamper(new_name)

    log_activity(new_name, "Renamed", f"Renamed '{old_name}' to '{new_name}'")

    save()
    return redirect("/")

@app.route("/edit_qty", methods=["POST"])
@protected_route
def edit_qty():


    item = preliminaries()

    quantity = request.form["quantity"].strip()

    inventory_dictionary = get_user_data("inventory_dictionary") # Get the inventory dictionary for the current user
    if item in inventory_dictionary and quantity:
        inventory_dictionary[item]["quantity"] = max(0, int(quantity))
        time_stamper(item)

    log_activity(item, "Quantity Edited", f"Set quantity to {quantity}")

    save()
    return redirect("/")

@app.route("/add", methods=["POST"])
@protected_route
def add_item():

    item = preliminaries()

    quantity = request.form["quantity"].strip()
    category = request.form["category"].strip().title() or "Uncategorized"


    if not item or not quantity or not category:
        return redirect("/")
    
    quantity = int(quantity)

    inventory_dictionary = get_user_data("inventory_dictionary")
    categories_and_colors = get_user_data("categories_and_colors")

    if item in inventory_dictionary:
        if inventory_dictionary[item]["quantity"] + quantity < 0:
            return redirect("/") # Prevent adding a negative quantity that would drop below 0
        else:
            pass # Quantity is valid, proceed with adding/updating

    if item in inventory_dictionary:
        inventory_dictionary[item]["quantity"] += quantity  
    else:
        inventory_dictionary[item] = {"quantity": quantity, "category": category} 
    
    if category not in categories_and_colors:
        categories_and_colors[category] = "#000000"
        set_user_data("categories_and_colors", categories_and_colors)
        save_categories_and_colors()
    time_stamper(item)

    log_activity(item, "Added/Updated", f"Added {quantity} in category '{category}'")

    save()
    return redirect("/")

@app.route("/add_new_category", methods=["POST"])
@protected_route
def add_category_if_not_exists():

    categories_and_colors = get_user_data("categories_and_colors") # Get the categories and colors for the current user

    category = request.form["category"].strip().title()
    if category and category not in categories_and_colors:
        categories_and_colors[category] = "#000000"  # Default color for new categories, also 
        save_categories_and_colors()
    return redirect("/categories")

@app.route("/stage_increase", methods=["POST"])
@protected_route
def stage_increase():
    pending_changes = get_user_data("pending_changes")
    item = request.form["item"].strip().title()
    if pending_changes and pending_changes["items"] == item:
        pending_changes["change"] += 1
    else:
        pending_changes = {"items": item, "change": 1}
    set_user_data("pending_changes", pending_changes)
    return redirect("/")


@app.route("/stage_decrease", methods=["POST"])
@protected_route
def stage_decrease():
    pending_changes = get_user_data("pending_changes")
    inventory_dictionary = get_user_data("inventory_dictionary") # Get the inventory dictionary for the current user
    
    item = request.form["item"].strip().title()
    current_qty = inventory_dictionary.get(item, {}).get("quantity", 0)
    if pending_changes and pending_changes["items"] == item:
        if current_qty + pending_changes["change"] > 0: # Prevent staging a decrease that would drop below 0
            pending_changes["change"] -= 1    
    else:
        if current_qty > 0: # Only stage a decrease if there's at least 1 item to decrease
            pending_changes = {"items": item, "change": -1}
    set_user_data("pending_changes", pending_changes)
    return redirect("/")

@app.route("/confirm", methods=["POST"])
@protected_route
def confirm_changes():
    
    pending_changes = get_user_data("pending_changes")
    inventory_dictionary = get_user_data("inventory_dictionary")
    future = get_user_data("future")

    old_qty = inventory_dictionary.get(pending_changes["items"], {}).get("quantity", 0) if pending_changes else 0

    if pending_changes:
        item = pending_changes["items"]
        change = pending_changes["change"]
        snapshot()  # Save state before applying changes for undo functionality
        future.clear()  # Clear redo history on new change
        if item in inventory_dictionary:
            new_qty = max(0, inventory_dictionary[item]["quantity"] + change) # Ensure quantity doesn't go below 0
            inventory_dictionary[item]["quantity"] = new_qty

            log_activity(item, "Quantity Increased" if change > 0 else "Quantity Decreased", f"from {old_qty} to {new_qty} (staged change of {change:+})")

            time_stamper(item)
            save()
        set_user_data("pending_changes", {})  # Clear pending changes after confirming

    

    return redirect("/")

@app.route("/cancel", methods=["POST"])
@protected_route
def cancel_changes():

    set_user_data("pending_changes", {})  # Update the user data with the cleared pending changes

    return redirect("/")


@app.route("/remove", methods=["POST"])
@protected_route
def remove():


    item = preliminaries()

    log_activity(item, "Removed", f"Removed item from inventory")

    inventory_dictionary = get_user_data("inventory_dictionary")
    inventory_dictionary.pop(item, None) 

    save()
    return redirect("/")

@app.route("/set_threshold", methods=["POST"])
@protected_route
def set_threshold():
    threshold = request.form["threshold"].strip()
    if threshold.isdigit():
        set_user_data("low_item_threshold", int(threshold))

    log_activity("Low Item Threshold", "Threshold Updated", f"Set low item threshold to {threshold}")

    return redirect("/")

def warning_alert(quantity):
    low_item_threshold = get_user_data("low_item_threshold") # Get the low item threshold for the current user
    if quantity < low_item_threshold and quantity > 0:
        return " ⚠️ "
    elif quantity == 0:
        return " ❌ "
    return ""

#
@app.route("/undo", methods=["POST"])
@protected_route
def undo():

    history = get_user_data("history") # Get the history list for the current user
    inventory_dictionary = get_user_data("inventory_dictionary") # Get the inventory dictionary for the current
    future = get_user_data("future") # Get the future list for the current user

    if history:
        start = time.perf_counter() # Start timer to measure how long the undo operation takes

        future.append(copy.deepcopy(inventory_dictionary))  # Save current state to future before undoing
        restored_inventory_dictionary = history.pop()  # Revert to the last state in history
        set_user_data("inventory_dictionary", restored_inventory_dictionary)  # Update the inventory dictionary in user data with the reverted state
        save()

        end = time.perf_counter() # End timer after undo operation is complete
        elapsed_time = (end - start) * 1000  # Convert to milliseconds
        print(f"Undo operation took {elapsed_time:.4f} ms") # Print
    return redirect("/")

@app.route("/redo", methods=["POST"])
@protected_route
def redo():

    history = get_user_data("history") # Get the history list for the current user
    inventory_dictionary = get_user_data("inventory_dictionary") # Get the inventory dictionary for the current
    future = get_user_data("future") # Get the future list for the current user

    if future:
        history.append(copy.deepcopy(inventory_dictionary))  # Save current state to history before redoing
        restored_inventory_dictionary = future.pop()  # Revert to the last state in future
        set_user_data("inventory_dictionary", restored_inventory_dictionary)  # Update the inventory dictionary in user data with the reverted state
        save()
    return redirect("/")    

@app.route("/export")
@protected_route
def export():

    inventory_dictionary = get_user_data("inventory_dictionary") # Get the inventory dictionary for the current user

    with open("inventory.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Item", "Quantity", "Category", "Last Modified"])
        for item, data in inventory_dictionary.items():
            writer.writerow([item, data["quantity"], data["category"], data.get("last_modified", "Never")])
    return redirect("/")

@app.route("/change_category_badge_color", methods=["POST"])
@protected_route
def category_color_change(): # This route handles the form submission from the category edit page to change the badge color for a category. It updates the categories_and_colors dictionary with the new color for the specified category and logs the activity.
    
    categories_and_colors = get_user_data("categories_and_colors") # Get the categories and colors for the current user

    category_name = request.form["category_name"].strip().title()
    hex_color = request.form["color"].strip() or "#000000"  # Default to white if no color provided

    categories_and_colors[category_name] = hex_color or "#000000"  # Update the color for the category


    save_categories_and_colors()  # Save the updated categories and colors to a file
    log_activity(category_name, "Category Edited", f"Set category to '{category_name}' with color {hex_color}")

    return redirect("/categories")

@protected_route
def category_html_return(category_name): # This function generates the HTML for displaying the category badge with the correct color based on the category. It checks the categories_and_colors dictionary for the hex color associated with the given category name and returns a styled span element with the category name and background color.
    
    categories_and_colors = get_user_data("categories_and_colors") # Get the categories and colors for the current user
    current_hex_color = categories_and_colors.get(category_name)

    #I want to make it so where if a category is added though the add function, the category is black by default, and if there is no category then the category is white by default, but if a category is added through the category edit page, it will be added with whatever color the user selects. This way we can have new categories added through the add item form without having to worry about setting up a color for them first, and they will just show up as black until the user decides to edit the category badge color.
    if current_hex_color is None: # If the category doesn't have a color set yet, assign a default based on whether it's "Uncategorized" or not
        if category_name == "Uncategorized":
            current_hex_color = "#FFFFFF"  # Default color for Uncategorized
        else:
            current_hex_color = "#000000"  # Default color for new categories
    save_categories_and_colors()  # Save the updated categories and colors to a file in case a new category was added without a color
    return f'<span style="font-size: 0.8em; color: #292929; background-color: {current_hex_color}; padding: 3.5px 7px; border-radius: 999px;">{category_name}</span>'

@app.route("/edit_category", methods=["POST"])
@protected_route
def edit_category():

    item = preliminaries()

    category = request.form["category"].strip().title()

    inventory_dictionary = get_user_data("inventory_dictionary") # Get the inventory dictionary for the current user
    if item in inventory_dictionary and category:
        inventory_dictionary[item]["category"] = category
        time_stamper(item)

    save()
    save_categories_and_colors()
    log_activity(item, "Category Edited", f"Set category to '{category}'")

    return redirect("/")

with app.app_context():
    load_users()

#if __name__ == "__main__":
    #app.run(debug=False)
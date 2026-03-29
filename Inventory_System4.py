import copy
import csv
import os

from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, json, render_template, render_template_string, request, redirect, session
from datetime import datetime

app = Flask(__name__)

app.secret_key = os.environ.get("SECRET_KEY", "dev-only-key-change-me")

inventory_dictionary = {}
low_item_threshold = 5
history = []
future = []
pending_changes = {}
activity_log = []  # List to store activity log entries
credentials = {}

app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# ---------------------------------------------------------------------------
# FILE FUNCTIONS
# ---------------------------------------------------------------------------

def save():
    global inventory_dictionary
    # Re-sort alphabetically every time we save
    inventory_dictionary = dict(sorted(inventory_dictionary.items()))
    with open("inventory.json", "w") as f:
        json.dump(inventory_dictionary, f)

def load():
    global inventory_dictionary
    try:
        with open("inventory.json", "r") as f:
            inventory_dictionary = json.load(f)
    except FileNotFoundError:
        pass

def load_log():
    global activity_log
    try:
        with open("activity_log.txt", "r") as f:
            activity_log = json.load(f)
    except FileNotFoundError:
        pass

def load_users():
    global credentials
    try:
        with open("users.json", "r") as f:
            credentials = json.load(f)
    except FileNotFoundError:
        default_password = os.getenv("ADMIN_PASSWORD", "admin123")# In a real application, you should set this environment variable to a secure password and not hardcode it
        credentials["TesterUser"] = {"password": generate_password_hash(default_password), "role": "admin"}
        save_users()

def save_log():
    global activity_log
    with open("activity_log.txt", "w") as f:
        json.dump(activity_log, f)
        f.write("\n")  # Add newline after each log entry for readability

def save_users():
    with open("users.json", "w") as f:
        json.dump(credentials, f)
#def save_history(): Eventually we could also save history/future stacks to files for persistence across rts, atm we'll just keep in memory

# ---------------------------------------------------------------------------
# UTILITY FUNCTIONS
# ---------------------------------------------------------------------------

def snapshot():
    global history
    history.append(copy.deepcopy(inventory_dictionary))  # Save a deep copy of the current state to history
    
def priliminaries():

    snapshot()          # save current state first
    future.clear()      # new change wipes redo history

    return request.form["item"].strip().title()

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def time_stamper(item):
    if item in inventory_dictionary:
        inventory_dictionary[item]["last_modified"] = now()

def protected_route(func):
    def wrapper(*args, **kwargs):
        username, role = get_current_user()
        if not username:
            return redirect("/login")
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper

def log_activity(item, action, detail):
    activity_log.append({
        "timestamp": now(),
        "action": action,
        "item": item,
        "detail": detail
    })
    save_log()

def get_current_user():
    return session.get("username"), session.get("role")

# ---------------------------------------------------------------------------
# LOGIN PAGE/LOGOUT ROUTE
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():

    error = None
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        user = credentials.get(username)
        if user and check_password_hash(user["password"], password):
            session["username"] = username
            session["role"] = user.get("role", "user")
            return redirect("/")
        error = "Invalid username or password"
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ---------------------------------------------------------------------------
# MAIN ROUTES (HOME AND LOG PAGE)
# ---------------------------------------------------------------------------

@app.route("/")
@protected_route
def home():


    if not session.get("username"):
        return redirect("/login")
    
    username, role = get_current_user()

    rows = ""
    qty_display = ""
    action_buttons = ""

    for item, data in inventory_dictionary.items():

        amount = data["quantity"]
        category = data["category"]
        warning = warning_alert(amount)
        category = category_badge(category)

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

            <td>{category}</td>

            <td>{data.get("last_modified", "Never")}</td>

            <td>{action_buttons}</td>

        </tr>
        """
    
    return render_template("home.html", rows=rows, username=username, role=role, low_item_threshold=low_item_threshold)

@app.route("/log")
@protected_route
def log_page():

    rows = ""

    for entry in reversed(activity_log):  # Show most recent first
        rows += f"""
        <tr>
            <td class="timestamp">{entry['timestamp']}</td>
            <td><strong>{entry['item']}</strong></td>
            <td>{entry['action']}</td>
            <td style="color:aeaeae;">{entry['detail']}</td>
        </tr>
        """
    return render_template("logs.html", rows=rows)

# ---------------------------------------------------------------------------
# INVENTORY LOGIC FUNCTIONS (ADD, EDIT, REMOVE, THRESHOLD, UNDO/REDO)
# ---------------------------------------------------------------------------

@app.route("/edit_name", methods=["POST"])
@protected_route
def edit_name():

    priliminaries()

    old_name = request.form["old_name"].strip().title()
    new_name = request.form["new_name"].strip().title()

    # Only update if both names are valid and the item actually exists
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

    item = priliminaries()

    quantity = request.form["quantity"].strip()

    if item in inventory_dictionary and quantity:
        inventory_dictionary[item]["quantity"] = max(0, int(quantity))
        time_stamper(item)

    log_activity(item, "Quantity Edited", f"Set quantity to {quantity}")

    save()
    return redirect("/")

@app.route("/add", methods=["POST"])
@protected_route
def add_item():

    item = priliminaries()

    quantity = request.form["quantity"].strip()
    category = request.form["category"].strip().title() or "Uncategorized"

    if not item or not quantity or not category:
        return redirect("/")
    
    quantity = int(quantity) 

    if item in inventory_dictionary:
        inventory_dictionary[item]["quantity"] += quantity  # item exists, add to it
    else:
        inventory_dictionary[item] = {"quantity": quantity, "category": category}   # new item, set it

    time_stamper(item)

    log_activity(item, "Added/Updated", f"Added {quantity} in category '{category}'")

    save()
    return redirect("/")

@app.route("/stage_increase", methods=["POST"])
@protected_route
def stage_increase():
    global pending_changes
    item = request.form["item"].strip().title()
    if pending_changes and pending_changes["items"] == item:
        pending_changes["change"] += 1
    else:
        pending_changes = {"items": item, "change": 1}
    
    return redirect("/")


@app.route("/stage_decrease", methods=["POST"])
@protected_route
def stage_decrease():
    global pending_changes
    item = request.form["item"].strip().title()
    current_qty = inventory_dictionary.get(item, {}).get("quantity", 0)
    if pending_changes and pending_changes["items"] == item:
        if current_qty + pending_changes["change"] > 0: # Prevent staging a decrease that would drop below 0
            pending_changes["change"] -= 1    
    else:
        if current_qty > 0: # Only stage a decrease if there's at least 1 item to decrease
            pending_changes = {"items": item, "change": -1}
    
    return redirect("/")

@app.route("/confirm", methods=["POST"])
@protected_route
def confirm_changes():
    global pending_changes, inventory_dictionary

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
        pending_changes = {}  # Clear pending changes after confirming

    

    return redirect("/")

@app.route("/cancel", methods=["POST"])
@protected_route
def cancel_changes():
    global pending_changes
    pending_changes = {}  # Clear pending changes without applying them
    
    return redirect("/")


@app.route("/remove", methods=["POST"])
@protected_route
def remove():

    item = priliminaries()

    log_activity(item, "Removed", f"Removed item from inventory")

    inventory_dictionary.pop(item, None)

    save()
    return redirect("/")

@app.route("/set_threshold", methods=["POST"])
@protected_route
def set_threshold():
    global low_item_threshold
    threshold = request.form["threshold"].strip()
    if threshold.isdigit():
        low_item_threshold = int(threshold)
    return redirect("/")

def warning_alert(quantity):
    if quantity < low_item_threshold and quantity > 0:
        return " ⚠️ "
    elif quantity == 0:
        return " ❌ "
    return ""

@app.route("/undo", methods=["POST"])
@protected_route
def undo():
    global history, future, inventory_dictionary
    if history:
        future.append(copy.deepcopy(inventory_dictionary))  # Save current state to future before undoing
        inventory_dictionary = history.pop()  # Revert to the last state in history
        save()
    return redirect("/")

@app.route("/redo", methods=["POST"])
@protected_route
def redo():
    global history, future, inventory_dictionary
    if future:
        history.append(copy.deepcopy(inventory_dictionary))  # Save current state to history before redoing
        inventory_dictionary = future.pop()  # Revert to the last state in future
        save()
    return redirect("/")    

@app.route("/export")
@protected_route
def export():
    with open("inventory.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Item", "Quantity", "Category", "Last Modified"])
        for item, data in inventory_dictionary.items():
            writer.writerow([item, data["quantity"], data["category"], data.get("last_modified", "Never")])
    return redirect("/")

def category_badge(category):
    colors = {
        "Utensils": "#c6f7d3",
        "Food": "#dbeafe",
        "Meat": "#fee2e2",
        "Raw Materials": "#fef3c7",
        "Frozen": "#c8dffb",
        "Vegetables": "#e8d5f9"
    }
    color = colors.get(category, "black")
    return f'<span style="font-size: 0.8em; color: #292929; background-color: {color}; padding: 3.5px 7px; border-radius: 999px;">{category}</span>'

load()
load_log()
load_users()

if __name__ == "__main__":
    app.run(debug=False)
from fastapi import UploadFile, File, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
import io
import os
import json
from pydantic import BaseModel
from supabase import create_client, Client
import google.generativeai as genai

# --- 1. SETUP CREDENTIALS ---
# I removed the hardcoded keys from here. 
# It is dangerous to keep them in the file (anyone on GitHub can steal them!)
# We will rely entirely on Render's Environment Variables.
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# --- 2. SETUP CLIENTS ---
# If these crash locally, make sure you have a .env file or set vars in terminal
if not SUPABASE_URL:
    print("WARNING: Secrets not found. Did you set them in Render?")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GEMINI_API_KEY)

# CORRECTED: You were right! Using 2.5 Flash.
model = genai.GenerativeModel('gemini-2.5-flash')

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class VoiceCommand(BaseModel):
    text: str

@app.get("/")
def read_root():
    return {"status": "AI Brain is Online"}

# --- 3. THE MISSING ENDPOINT (CRITICAL FIX) ---
# Your HTML was trying to fetch this, but it didn't exist!
# This is why it said "Connection Failed".
@app.get("/inventory")
def get_inventory():
    response = supabase.table("inventory").select("*").execute()
    return response.data

@app.post("/voice-action")
def process_voice(command: VoiceCommand): 
    print(f"Received: {command.text}")

    try:
        # AI Prompt
        prompt = f"""
        Analyze this kitchen voice command: "{command.text}"
        Return JSON with actions.
        Format: {{ "actions": [ {{ "action_type": "USE", "item": "egg", "quantity": 2 }} ] }}
        
        IMPORTANT RULES:
        1. Output the "item" name in SINGULAR form (e.g. "egg", not "eggs").
        2. Output the "item" name in LOWERCASE (e.g. "milk", not "Milk").
        """
        
        response = model.generate_content(prompt)
        clean_text = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_text)
        
        # Update Database
        actions = data.get("actions", [])
        updates_made = []

        for action in actions:
            if action["action_type"] == "USE":
                item_name = action["item"]
                qty_used = action["quantity"]
                
                # Check DB
                db_item = supabase.table("inventory").select("*").ilike("item_name", item_name).execute()
                
                if db_item.data:
                    current_id = db_item.data[0]['id']
                    current_stock = db_item.data[0]['quantity']
                    new_stock = float(current_stock) - float(qty_used)
                    
                    supabase.table("inventory").update({"quantity": new_stock}).eq("id", current_id).execute()
                    updates_made.append(f"Updated {item_name}: {current_stock} -> {new_stock}")
                else:
                    updates_made.append(f"Error: Could not find '{item_name}' in inventory")

        return {"ai_analysis": data, "db_updates": updates_made}

    except Exception as e:
        return {"error": str(e)}
    
@app.post("/scan-bill")
async def scan_bill(file: UploadFile = File(...)):
    print(f"Received file: {file.filename}")
    
    try:
        contents = await file.read()
        image_part = {"mime_type": file.content_type, "data": contents}

        # Using 2.5 Flash for Vision as well
        vision_model = genai.GenerativeModel('gemini-2.5-flash') 
        
        prompt = """
        Look at this grocery bill. Extract all food items and quantities.
        Return a JSON with a list of items to ADD to inventory.
        Format: { "items": [ { "item": "milk", "quantity": 1, "unit": "liter" } ] }
        
        IMPORTANT:
        1. Use SINGULAR, LOWERCASE names (e.g. "egg", not "eggs").
        2. Ignore non-food items (taxes, plastic bags).
        """

        response = vision_model.generate_content([prompt, image_part])
        clean_text = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_text)

        items = data.get("items", [])
        logs = []

        for item in items:
            name = item["item"]
            qty = float(item["quantity"])
            
            existing = supabase.table("inventory").select("*").ilike("item_name", name).execute()
            
            if existing.data:
                current_qty = float(existing.data[0]['quantity'])
                new_qty = current_qty + qty
                supabase.table("inventory").update({"quantity": new_qty}).eq("id", existing.data[0]['id']).execute()
                logs.append(f"Added {qty} to {name} (Total: {new_qty})")
            else:
                supabase.table("inventory").insert({"item_name": name, "quantity": qty, "unit": "unit"}).execute()
                logs.append(f"Created new item: {name} ({qty})")

        return {"status": "success", "logs": logs, "scanned_data": data}

    except Exception as e:
        return {"error": str(e)}

# --- 4. SMART SHOPPING LIST ENDPOINT ---
@app.get("/shopping-list")
def get_shopping_list():
    # 1. Fetch all inventory
    response = supabase.table("inventory").select("*").execute()
    data = response.data
    
    shopping_list = []
    
    # 2. Logic: If Quantity < Threshold, add to list
    for item in data:
        # Get threshold (default to 2 if missing)
        safe_limit = item.get('threshold') or 2.0
        current_qty = float(item['quantity'])
        
        if current_qty < safe_limit:
            needed = safe_limit - current_qty
            shopping_list.append({
                "item_name": item['item_name'], # We use item_name to match your DB
                "quantity": round(current_qty, 2),
                "needed": round(needed, 2),
                "unit": item['unit']
            })
            
    return shopping_list


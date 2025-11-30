from fastapi import UploadFile, File, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
import io
import os
import json
from pydantic import BaseModel
from supabase import create_client, Client
import google.generativeai as genai

# --- CONFIG ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not SUPABASE_URL: print("WARNING: Secrets missing!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- DATA MODELS ---
class VoiceCommand(BaseModel):
    text: str

class ManualItem(BaseModel):
    item_name: str
    quantity: float
    unit: str

# --- ROUTES ---

@app.get("/")
def read_root(): return {"status": "Stockify V3 Online"}

@app.get("/inventory")
def get_inventory():
    # Sort alphabetically
    response = supabase.table("inventory").select("*").order('item_name').execute()
    return response.data

# NEW: Manual Add Endpoint
@app.post("/inventory/add")
def add_manual(item: ManualItem):
    # Check if item exists (Case insensitive)
    existing = supabase.table("inventory").select("*").ilike("item_name", item.item_name).execute()
    
    if existing.data:
        # Update existing
        current_qty = float(existing.data[0]['quantity'])
        new_qty = current_qty + item.quantity
        supabase.table("inventory").update({"quantity": new_qty}).eq("id", existing.data[0]['id']).execute()
        return {"status": "Updated", "new_qty": new_qty, "item": item.item_name}
    else:
        # Create new
        supabase.table("inventory").insert({
            "item_name": item.item_name, 
            "quantity": item.quantity, 
            "unit": item.unit
        }).execute()
        return {"status": "Created", "item": item.item_name}

# --- NEW: Manual Consume Endpoint (For Cook Tab) ---
@app.post("/inventory/consume")
def consume_manual(item: ManualItem):
    # 1. Find the item (Case insensitive)
    existing = supabase.table("inventory").select("*").ilike("item_name", item.item_name).execute()
    
    if existing.data:
        current_data = existing.data[0]
        current_qty = float(current_data['quantity'])
        
        # 2. Subtract quantity (ensure it doesn't go below 0)
        new_qty = max(0, current_qty - item.quantity)
        
        # 3. Update DB
        supabase.table("inventory").update({"quantity": new_qty}).eq("id", current_data['id']).execute()
        
        return {
            "status": "Consumed", 
            "item": item.item_name, 
            "previous": current_qty, 
            "new": new_qty
        }
    else:
        return {"status": "Error", "message": "Item not found in inventory"}

@app.get("/shopping-list")
def get_shopping_list():
    response = supabase.table("inventory").select("*").execute()
    data = response.data
    shopping_list = []
    
    for item in data:
        safe_limit = item.get('threshold') or 2.0
        current_qty = float(item['quantity'])
        if current_qty < safe_limit:
            needed = safe_limit - current_qty
            shopping_list.append({
                "item_name": item['item_name'],
                "quantity": round(current_qty, 2),
                "needed": round(needed, 2),
                "unit": item['unit']
            })
    return shopping_list

# --- AI ROUTES ---
@app.post("/voice-action")
def process_voice(command: VoiceCommand): 
    try:
        prompt = f"""
        Analyze kitchen command: "{command.text}"
        Return JSON actions: {{ "actions": [ {{ "action_type": "USE", "item": "egg", "quantity": 2 }} ] }}
        RULES: Singular, Lowercase item names.
        """
        res = model.generate_content(prompt)
        data = json.loads(res.text.replace("```json", "").replace("```", "").strip())
        
        actions = data.get("actions", [])
        logs = []
        for action in actions:
            if action["action_type"] == "USE":
                name = action["item"]
                qty = action["quantity"]
                db_item = supabase.table("inventory").select("*").ilike("item_name", name).execute()
                if db_item.data:
                    new_qty = float(db_item.data[0]['quantity']) - float(qty)
                    supabase.table("inventory").update({"quantity": new_qty}).eq("id", db_item.data[0]['id']).execute()
                    logs.append(f"Updated {name}")
                else:
                    logs.append(f"Could not find {name}")
        return {"ai_analysis": data, "logs": logs}
    except Exception as e: return {"error": str(e)}

@app.post("/scan-bill")
async def scan_bill(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        vision_model = genai.GenerativeModel('gemini-2.5-flash') 
        prompt = """
        Extract food items from bill. Return JSON: { "items": [ { "item": "milk", "quantity": 1, "unit": "liter" } ] }
        RULES: Singular, Lowercase names. Ignore taxes.
        """
        res = vision_model.generate_content([prompt, {"mime_type": file.content_type, "data": contents}])
        data = json.loads(res.text.replace("```json", "").replace("```", "").strip())
        
        items = data.get("items", [])
        logs = []
        for item in items:
            name = item["item"]
            qty = float(item["quantity"])
            existing = supabase.table("inventory").select("*").ilike("item_name", name).execute()
            if existing.data:
                new_qty = float(existing.data[0]['quantity']) + qty
                supabase.table("inventory").update({"quantity": new_qty}).eq("id", existing.data[0]['id']).execute()
            else:
                supabase.table("inventory").insert({"item_name": name, "quantity": qty, "unit": "unit"}).execute()
            logs.append(f"Added {name}")
        return {"status": "success", "logs": logs}
    except Exception as e: return {"error": str(e)}


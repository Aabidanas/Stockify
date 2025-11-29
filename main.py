import os
import json
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client
import google.generativeai as genai

# --- PASTE YOUR KEYS HERE ---
# Load keys from the "Environment" (The Cloud's Secret Vault)
# We use 'os.environ.get' so the code finds keys on Render automatically
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")


# --- SETUP CLIENTS ---
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GEMINI_API_KEY)
# Use 'gemini-pro' since we know it works for you
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

# --- THIS IS THE CRITICAL PART ---
# The word 'command' inside the parentheses below defines the variable!
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
                    updates_made.append(f"Error: Could not find '{item_name}' in inventory (Did you add it to Supabase?)")

        return {"ai_analysis": data, "db_updates": updates_made}

    except Exception as e:
        return {"error": str(e)}
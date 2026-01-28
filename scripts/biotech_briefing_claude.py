import anthropic
import json
from datetime import datetime
import os

def generate_biotech_briefing():
    """Generate biotech morning briefing using Claude with web search"""
    
    # Initialize Anthropic client
    client = anthropic.Anthropic(
        api_key=os.environ.get("ANTHROPIC_API_KEY")
    )
    
    # Create the prompt
    prompt = """Erstelle ein deutschsprachiges Biotech-Morning-Briefing für heute mit folgender Struktur:

1. Die 3-5 wichtigsten Biotech/Pharma News der letzten 24 Stunden
2. Relevante FDA-Zulassungen oder klinische Studienergebnisse
3. Bedeutende Unternehmens-Deals oder Finanzierungen
4. Kurze Marktübersicht (Biotech-Indizes falls relevant)

Für jeden Artikel bitte:
- Aussagekräftigen Titel
- Kurze Zusammenfassung (2-3 Sätze)
- Quelle mit URL
- Datum

Nutze aktuelle Web-Suche um echte, verifizierbare News zu finden. Keine erfundenen Artikel!"""

    # Make API call with web search enabled
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search"
        }],
        messages=[{
            "role": "user",
            "content": prompt
        }]
    )
    
    # Extract the text response
    briefing_text = ""
    for block in message.content:
        if block.type == "text":
            briefing_text += block.text
    
    # Structure the output as JSON
    briefing_data = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now().isoformat(),
        "briefing": briefing_text,
        "model": "claude-sonnet-4",
        "source": "Claude API with Web Search"
    }
    
    return briefing_data

def save_briefing(briefing_data):
    """Save briefing to JSON file"""
    with open('briefing.json', 'w', encoding='utf-8') as f:
        json.dump(briefing_data, f, ensure_ascii=False, indent=2)
    
    print("✅ Briefing successfully generated and saved!")
    print(f"📅 Date: {briefing_data['date']}")
    print(f"🤖 Model: {briefing_data['model']}")

if __name__ == "__main__":
    try:
        print("🔍 Generating Biotech Morning Briefing with Claude...")
        briefing = generate_biotech_briefing()
        save_briefing(briefing)
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        raise

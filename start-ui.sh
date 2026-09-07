#!/bin/bash
# Start script for Bewerbungsagent Web UI

echo "🤖 Bewerbungsagent Web UI"
echo "=========================="
echo ""

# Check if streamlit is installed
if ! python -c "import streamlit" 2>/dev/null; then
    echo "❌ Streamlit nicht gefunden!"
    echo ""
    echo "Installiere UI-Abhängigkeiten:"
    echo "  pip install -e \".[ui]\""
    echo ""
    exit 1
fi

# Check if profile exists
if [ ! -f "config/profil.yaml" ] && [ ! -f "$HOME/.config/bewerbungsagent/profil.yaml" ]; then
    echo "⚠️  Profil nicht gefunden!"
    echo ""
    echo "Öffne nach dem Start die Seite 👤 Profil und lege es dort über die UI an."
    echo ""
fi

# Check if .env exists
if [ ! -f ".env" ]; then
    echo "⚠️  .env Datei nicht gefunden!"
    echo ""
    echo "Erstelle .env mit API-Key:"
    echo "  cp .env.example .env"
    echo "  # Trage ANTHROPIC_API_KEY in .env ein"
    echo ""
fi

echo "🚀 Starte Web UI..."
echo "   Öffne im Browser: http://localhost:8501"
echo ""

streamlit run app.py

# prediction/views.py
import json
import os
import re
from django.shortcuts import render

# Chemin vers le fichier JSON
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FORECAST_FILE = os.path.join(BASE_DIR, 'forecast_data.json')

MONTH_TRANSLATIONS = {
    'January': 'Janvier',
    'February': 'Février',
    'March': 'Mars',
    'April': 'Avril',
    'May': 'Mai',
    'June': 'Juin',
    'July': 'Juillet',
    'August': 'Août',
    'September': 'Septembre',
    'October': 'Octobre',
    'November': 'Novembre',
    'December': 'Décembre',
    'Jan': 'Janv',
    'Feb': 'Fév',
    'Mar': 'Mars',
    'Apr': 'Avr',
    'Jun': 'Juin',
    'Jul': 'Juil',
    'Aug': 'Août',
    'Sep': 'Sept',
    'Oct': 'Oct',
    'Nov': 'Nov',
    'Dec': 'Déc',
}

def translate_month_string(label):
    translated = label
    for english, french in sorted(MONTH_TRANSLATIONS.items(), key=lambda item: -len(item[0])):
        translated = re.sub(r'\b' + re.escape(english) + r'\b', french, translated)
    return translated


def load_forecast_data():
    with open(FORECAST_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)

    predictions = [
        {
            **prediction,
            'mois': translate_month_string(prediction['mois']),
        }
        for prediction in data['predictions']
    ]

    historique = [
        {
            **entry,
            'mois': translate_month_string(entry['mois']),
        }
        for entry in data['historique']
    ]

    return {
        'predictions': predictions,
        'historique': historique,
        'modele': data['modele'],
        'mape': data['mape'],
        'derniere_date': translate_month_string(data['derniere_donnee_reelle']),
        'nb_mois': len(predictions),
    }


def prediction_view(request):
    context = load_forecast_data()
    return render(request, 'prediction_results.html', context)

#!/usr/bin/env python3
"""
Convert Diabet Logger CSV export to Diabetes:M ENTRIES import format.

Usage:
  python3 logger2diabetesm.py <logger-export.csv> [-o output.csv]

Maps Logger types to Diabetes:M columns:
  BG (non-CGM)   → glucose + finger test
  BG [CGM]       → glucose + is_sensor=true
  FAST            → carb_bolus + correction_bolus (split by meta)
  SLOW            → basal
  MEAL            → carbs (from carbs_g)
  KETONES         → ketones
  ACTIVITY        → exercise
  ALCOHOL         → notes only (no Diabetes:M field)
  DEVICE_CHANGE   → notes only
"""
import csv, json, sys, os
from datetime import datetime, timezone

def parse_meta(raw: str) -> dict:
    if not raw or raw.strip() == '':
        return {}
    try:
        return json.loads(raw)
    except:
        return {}

def guess_category(hour: int) -> int:
    """Rough time-of-day → Diabetes:M category."""
    if hour < 5: return 8     # Night
    if hour < 10: return 1    # Pre-Breakfast (approximate)
    if hour < 12: return 3    # Pre-Lunch
    if hour < 14: return 4    # Post-Lunch
    if hour < 17: return 5    # Pre-Dinner
    if hour < 20: return 6    # Post-Dinner
    if hour < 22: return 7    # Pre-Sleep
    return 8                   # Night

# Diabetes:M insulin type codes (common ones)
INSULIN_TYPES = {
    'fiasp': 38, 'novorapid': 3, 'humalog': 1, 'apidra': 11,
    'tresiba': 32, 'lantus': 4, 'levemir': 5, 'toujeo': 33,
    'other': 0
}

# Diabetes:M injection sites
BOLUS_SITES = {'pump': 1034, 'pen': -1, 'iv': -1}
BASAL_SITES = {'pump': 1034, 'pen': 1033}

# Diabetes:M finger test sites
FINGER_SITES = {'own': 26, 'hospital': 27, 'cgm_manual': 26}

HEADER_LINE = '"name:DIABETES_M","version:2.0.0","export:Entries"'
COLUMNS = [
    'DateTimeFormatted', 'glucose', 'carbs', 'proteins', 'fats', 'calories',
    'carb_bolus', 'correction_bolus', 'extended_bolus', 'extended_bolus_duration',
    'basal', 'basal_is_rate', 'bolus_insulin_type', 'basal_insulin_type',
    'weight_entry', 'weight', 'category', 'category_name',
    'carb_ratio_factor', 'insulin_sensitivity_factor', 'notes', 'is_sensor',
    'pressure_sys', 'pressure_dia', 'pulse',
    'injection_bolus_site', 'injection_basal_site', 'finger_test_site',
    'ketones', 'google_fit_source', 'timezone',
    'exercise_index', 'exercise_comment', 'exercise_duration',
    'medications', 'food', 'us_units', 'hba1c',
    'cholesterol_total', 'cholesterol_ldl', 'cholesterol_hdl', 'triglycerides',
    'microalbumin_test_type', 'microalbumin', 'creatinine_clearance', 'egfr',
    'cystatin_c', 'albumin', 'creatinine', 'calcium', 'total_protein',
    'sodium', 'potassium', 'bicarbonate', 'chloride',
    'alp', 'alt', 'ast', 'bilirubin', 'bun'
]

def empty_row() -> dict:
    row = {c: '' for c in COLUMNS}
    row.update({
        'glucose': '0.0', 'carbs': '0.0', 'proteins': '0.0', 'fats': '0.0', 'calories': '0.0',
        'carb_bolus': '0.0', 'correction_bolus': '0.0', 'extended_bolus': '0.0',
        'extended_bolus_duration': '0',
        'basal': '0.0', 'basal_is_rate': 'false',
        'bolus_insulin_type': '0', 'basal_insulin_type': '0',
        'weight_entry': '0.0', 'weight': '70.0',
        'category': '0',
        'carb_ratio_factor': '12.0', 'insulin_sensitivity_factor': '2.5',
        'is_sensor': 'false',
        'pressure_sys': '0', 'pressure_dia': '0', 'pulse': '0',
        'injection_bolus_site': '-1', 'injection_basal_site': '-1',
        'finger_test_site': '-1',
        'ketones': '0.0',
        'timezone': 'Europe/Sofia',
        'exercise_index': '0', 'exercise_duration': '0',
        'us_units': '',
    })
    return row

def convert(logger_csv: str, output_csv: str):
    with open(logger_csv, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        entries = list(reader)
    
    rows = []
    stats = {'bg': 0, 'cgm': 0, 'fast': 0, 'slow': 0, 'meal': 0, 'ketones': 0,
             'activity': 0, 'alcohol': 0, 'device': 0, 'skipped': 0}
    
    for e in entries:
        t = e['type'].upper()
        meta = parse_meta(e.get('meta', ''))
        
        # Parse time
        try:
            dt = datetime.fromisoformat(e['time_utc'])
            local_str = dt.astimezone().strftime('%Y-%m-%d %H:%M:%S')
            hour = dt.astimezone().hour
        except:
            stats['skipped'] += 1
            continue
        
        row = empty_row()
        row['DateTimeFormatted'] = local_str
        row['category'] = str(guess_category(hour))
        row['notes'] = e.get('notes', '')
        
        val = e.get('value', '0')
        try:
            val_f = float(val)
        except:
            val_f = 0
        
        if t == 'BG':
            is_cgm = '[CGM]' in (e.get('notes', ''))
            row['glucose'] = str(val_f)
            row['is_sensor'] = 'true' if is_cgm else 'false'
            if not is_cgm:
                source = meta.get('source', 'own')
                row['finger_test_site'] = str(FINGER_SITES.get(source, 26))
            if is_cgm:
                stats['cgm'] += 1
            else:
                stats['bg'] += 1
        
        elif t == 'FAST':
            delivery = meta.get('delivery', 'pen')
            insulin_type = meta.get('insulin_type', 'fiasp')
            bolus_kind = meta.get('bolus_kind', 'normal')
            
            # Split: if there's carbs_g in notes or meal nearby, it's carb_bolus; otherwise correction
            carbs_g = float(e.get('carbs_g', 0) or 0)
            if carbs_g > 0 or 'XE' in (e.get('notes', '')):
                row['carb_bolus'] = str(val_f)
            else:
                row['correction_bolus'] = str(val_f)
            
            if bolus_kind in ('extended', 'combo'):
                dur = meta.get('bolus_duration_min', 0)
                row['extended_bolus'] = str(val_f * 0.4)  # rough 60/40 split
                row['extended_bolus_duration'] = str(dur)
                row['carb_bolus'] = str(val_f * 0.6)
            
            row['bolus_insulin_type'] = str(INSULIN_TYPES.get(insulin_type, 0))
            row['injection_bolus_site'] = str(BOLUS_SITES.get(delivery, -1))
            stats['fast'] += 1
        
        elif t == 'SLOW':
            delivery = meta.get('delivery', 'pen')
            insulin_type = meta.get('insulin_type', 'tresiba')
            row['basal'] = str(val_f)
            row['basal_is_rate'] = 'false'  # absolute dose, not rate
            row['basal_insulin_type'] = str(INSULIN_TYPES.get(insulin_type, 0))
            row['injection_basal_site'] = str(BASAL_SITES.get(delivery, -1))
            stats['slow'] += 1
        
        elif t == 'MEAL':
            carbs_g = float(e.get('carbs_g', 0) or 0)
            row['carbs'] = str(carbs_g) if carbs_g > 0 else str(val_f * 12)  # fallback: XE * 12g
            stats['meal'] += 1
        
        elif t == 'KETONES':
            row['ketones'] = str(val_f)
            stats['ketones'] += 1
        
        elif t == 'ACTIVITY':
            row['exercise_duration'] = str(int(val_f))
            intensity = meta.get('intensity', 'moderate')
            row['exercise_index'] = {'light': 1, 'moderate': 2, 'intense': 3}.get(intensity, 2)
            row['exercise_comment'] = e.get('notes', '')
            stats['activity'] += 1
        
        elif t == 'ALCOHOL':
            row['notes'] = f"Алкохол: {val_f} ед. " + e.get('notes', '')
            stats['alcohol'] += 1
        
        elif t == 'DEVICE_CHANGE':
            device_type = meta.get('device_type', '')
            row['notes'] = f"[{device_type}] " + e.get('notes', '')
            stats['device'] += 1
        
        else:
            stats['skipped'] += 1
            continue
        
        rows.append(row)
    
    # Sort by time
    rows.sort(key=lambda r: r['DateTimeFormatted'])
    
    # Write
    with open(output_csv, 'w', encoding='utf-8', newline='') as f:
        f.write(HEADER_LINE + '\n')
        writer = csv.DictWriter(f, fieldnames=COLUMNS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)
    
    print(f'Конвертирано: {len(rows)} записа → {output_csv}')
    print(f'  BG (ръчни): {stats["bg"]}, CGM: {stats["cgm"]}, Бърз: {stats["fast"]}, '
          f'Бавен: {stats["slow"]}, Хранене: {stats["meal"]}, Кетони: {stats["ketones"]}')
    print(f'  Активност: {stats["activity"]}, Алкохол: {stats["alcohol"]}, '
          f'Устройство: {stats["device"]}, Пропуснати: {stats["skipped"]}')

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(f'Usage: {sys.argv[0]} <logger-export.csv> [-o output.csv]')
        sys.exit(1)
    
    inp = sys.argv[1]
    out = None
    if '-o' in sys.argv:
        out = sys.argv[sys.argv.index('-o') + 1]
    else:
        base = os.path.splitext(os.path.basename(inp))[0]
        out = f'DiabetesM_import_{base}.csv'
    
    convert(inp, out)

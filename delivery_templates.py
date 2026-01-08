"""
Delivery Template Engine

Renders client-specific delivery filenames using templates.
"""
import re
from datetime import datetime
from pathlib import Path


MONTH_NAMES = {
    1: "january", 2: "february", 3: "march", 4: "april",
    5: "may", 6: "june", 7: "july", 8: "august",
    9: "september", 10: "october", 11: "november", 12: "december"
}


def extract_title(original_filename: str) -> str:
    """
    Extract title from original filename.
    Example: InTouch_December2024.mp4 -> December2024
    """
    stem = Path(original_filename).stem
    # Remove client prefix if detected
    parts = stem.split("_", 1)
    if len(parts) > 1:
        return parts[1]
    return stem


def render_template(template: str, client: str, original_filename: str, delivery_date: datetime = None) -> str:
    """
    Render delivery filename template with tokens.
    
    Tokens:
        {client} - Client name
        {title} - Extracted from original filename
        {date_YYYY_MM_DD} - 2024_12_28
        {date_MM-DD-YY} - 12-28-24
        {date_DD_month_YYYY} - 28_december_2024
    """
    if delivery_date is None:
        delivery_date = datetime.now()
    
    title = extract_title(original_filename)
    
    # Clean title for filename safety
    title = re.sub(r'[^\w\s-]', '', title)
    title = re.sub(r'[-\s]+', '_', title).strip('_')
    
    # Build replacement tokens
    tokens = {
        "client": client,
        "title": title,
        "date_YYYY_MM_DD": delivery_date.strftime("%Y_%m_%d"),
        "date_MM-DD-YY": delivery_date.strftime("%m-%d-%y"),
        "date_DD_month_YYYY": f"{delivery_date.day}_{MONTH_NAMES[delivery_date.month]}_{delivery_date.year}"
    }
    
    # Replace tokens
    result = template
    for token_name, token_value in tokens.items():
        result = result.replace(f"{{{token_name}}}", token_value)
    
    return result

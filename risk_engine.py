def get_risk_analysis(medicines):
    meds = [m.lower() for m in medicines]
    
    # Correct Medical Logic for Activity 4.1
    if "aspirin" in meds and "warfarin" in meds:
        return "⚠️ High Risk: Combining Aspirin and Warfarin increases bleeding risk. Consult a doctor."
    
    if "aspirin" in meds and "ibuprofen" in meds:
        return "⚠️ Warning: Both are NSAIDs. Taking them together can cause stomach issues."
    
    return f"Analysis Complete: No major interactions detected for {', '.join(medicines)}."
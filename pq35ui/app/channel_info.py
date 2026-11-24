"""What each logged value shows, in plain words.

describe(value) gives the text for one catalog value (Logger tooltips and info box).
ie_help_html() builds the Help topic for the Integrated Engineering logging format from
log_export.IE_COLUMNS (so it always matches what the export writes) and Integrated
Engineering's own glossary, copied word for word."""
from html import escape

# By catalog id. Values without an entry get a description from their kind and unit.
DESCRIPTIONS = {
    # --- engine: speed, load, air ---
    "engine.rpm": "How fast the crankshaft is turning.",
    "engine.load.actual": "How much air the engine is actually taking in per stroke, compared with what "
        "it would take in without a turbo. Over 100% means the turbo is boosting.",
    "engine.load.spec": "The load the ECU is asking for (driver request after torque limits).",
    "engine.load.corrected": "The requested load after the ECU's corrections.",
    "engine.maf": "Air entering the engine, measured by the mass air flow sensor (G70), in grams per second.",
    "engine.intake_temperature": "Air temperature in the intake manifold, after the intercooler (G42). "
        "Hot intake air makes the ECU pull timing.",
    "engine.atmospheric_pressure": "Outside air pressure. Subtract it from boost (absolute) to get boost "
        "above atmosphere.",
    "engine.altitude_correction_factor": "How much the ECU corrects for altitude (thinner air).",
    "engine.throttle_position": "How far the throttle valve is open: 0% closed, 100% wide open.",
    "engine.pedal_position": "How far the accelerator pedal is pressed (G79).",
    "engine.pedal.sensor2.position": "Accelerator pedal second sensor (G185), used as a cross-check.",
    "engine.throttle.sensor1.position": "Throttle body angle sensor 1 (G187).",
    "engine.throttle.sensor2.position": "Throttle body angle sensor 2 (G188), used as a cross-check.",
    # --- boost ---
    "engine.boost.actual_absolute": "Measured pressure in the intake manifold, absolute (includes the ~1000 mbar "
        "of the atmosphere). About 1000 = no boost.",
    "engine.boost.spec_absolute": "The boost pressure the ECU is asking for, absolute. Compare with actual: "
        "actual well below requested means a boost leak or tired turbo; above means overboost.",
    "engine.n75_duty": "How hard the ECU drives the N75 boost control valve. Higher = more boost. Near 100% "
        "while boost is still low means the turbo can't make the requested boost.",
    "engine.boost.control_adaptation": "Long-term correction the ECU has learned for boost control.",
    # --- fuel ---
    "engine.injection_time": "How long each direct injector stays open per stroke, in milliseconds. "
        "0 on overrun (fuel cut).",
    "engine.fuel_rail.actual": "Measured pressure in the high-pressure fuel rail (bar). Should follow the "
        "setpoint; dropping well below it under load points to the high-pressure pump or its cam follower.",
    "engine.fuel_rail.spec": "The high-pressure fuel rail pressure the ECU is asking for (bar).",
    "engine.fuel_pump_duty": "Duty of the high-pressure pump's quantity control valve (N276). Near 100% means "
        "the pump is at its limit.",
    "engine.fuel_temperature.calculated": "Fuel temperature, as calculated by the ECU.",
    "engine.fuel_quantity_valve.closing_angle": "Crank angle at which the high-pressure pump's quantity valve closes.",
    "engine.fuel_quantity_valve.opening_angle": "Crank angle at which the high-pressure pump's quantity valve opens.",
    "engine.fuel_high_pressure.adaptation": "Long-term correction learned for the high-pressure fuel system.",
    "engine.fuel_high_pressure.controller_portion": "How much the rail pressure controller is correcting right now.",
    "engine.correction_factor.fuel": "Fuel correction factor the ECU applies.",
    "engine.correction_factor.coolant_temperature": "Fuel correction for engine temperature (more fuel when cold).",
    "engine.correction_factor.intake_temperature": "Correction for intake air temperature.",
    # --- lambda / trims ---
    "engine.lambda.control.bank1": "Short-term fuel trim. Positive = the ECU is adding fuel, negative = taking "
        "fuel away, to hit its target mixture.",
    "engine.lambda.bank1.actual": "Measured air-fuel ratio as lambda. 1.00 = ideal (stoichiometric), below 1 = rich, "
        "above 1 = lean.",
    "engine.lambda.bank1.specified": "The air-fuel ratio (lambda) the ECU is aiming for. Drops below 1 (richer) "
        "under full load.",
    "engine.lambda.bank1.sensor1.adaptation.idle": "Long-term fuel trim at idle. Large values point to an air "
        "leak or fuelling problem at low load.",
    "engine.lambda.bank1.sensor1.adaptation.partial": "Long-term fuel trim at part load. Large values point to "
        "MAF or fuelling problems.",
    "engine.lambda.bank1.sensor1.control": "Lambda control value from the front (pre-cat) sensor.",
    "engine.lambda.bank1.sensor1.voltage": "Front (pre-cat) oxygen sensor signal.",
    "engine.lambda.bank1.sensor2.voltage": "Rear (post-cat) oxygen sensor signal; steady when the cat works.",
    "engine.lambda.control.generic": "Lambda control value (group 99).",
    "engine.lambda.bank1.enrichment_factor": "Extra enrichment the ECU adds (e.g. to protect components when hot).",
    # --- ignition / knock ---
    "engine.ignition_timing": "Spark timing in degrees before top dead centre, after all corrections. "
        "Higher = more advance.",
    "engine.timing_retard.cylinder1": "How much timing knock control is pulling on cylinder 1. 0 = none; "
        "a few degrees now and then is normal, constant large pulls are not.",
    "engine.timing_retard.cylinder2": "How much timing knock control is pulling on cylinder 2. 0 = none.",
    "engine.timing_retard.cylinder3": "How much timing knock control is pulling on cylinder 3. 0 = none.",
    "engine.timing_retard.cylinder4": "How much timing knock control is pulling on cylinder 4. 0 = none.",
    "engine.knock.cylinder1.sensor_voltage": "Knock sensor signal for cylinder 1. Compare cylinders: one "
        "standing out is worth a look.",
    "engine.knock.cylinder2.sensor_voltage": "Knock sensor signal for cylinder 2.",
    "engine.knock.cylinder3.sensor_voltage": "Knock sensor signal for cylinder 3.",
    "engine.knock.cylinder4.sensor_voltage": "Knock sensor signal for cylinder 4.",
    # --- torque ---
    "engine.torque.actual": "The engine torque the ECU calculates (a model, not a measurement), from group 120.",
    "engine.torque.actual.group122": "The engine torque the ECU calculates (a model, not a measurement), "
        "read from the transmission group 122.",
    "engine.torque.asr_specified": "Torque limit asked for by traction control (ASR). A high value means "
        "no intervention.",
    "engine.torque.transmission_specified": "Torque limit asked for by the transmission. Drops briefly "
        "during shifts; a high value means no limit.",
    "engine.traction_control.status": "Traction control intervention status (a code).",
    "engine.transmission.status": "Whether the transmission is asking the engine to cut torque "
                                  "(e.g. during a shift): a status code, no unit.",
    # --- temperatures, electrical, vehicle ---
    "engine.coolant_temperature": "Engine coolant temperature (G62). About 90 °C when warm.",
    "engine.oil_temperature": "Engine oil temperature.",
    "engine.outlet_temperature": "Coolant temperature at the engine outlet.",
    "engine.radiator_outlet_temperature": "Coolant temperature at the radiator outlet.",
    "engine.catalyst.bank1.temperature": "Catalytic converter temperature (modelled).",
    "engine.exhaust_temperature.projected": "Exhaust gas temperature as modelled by the ECU.",
    "engine.start_temperature": "Coolant temperature when the engine was started.",
    "ambient.temperature": "Outside air temperature.",
    "engine.terminal30_voltage": "Battery voltage at the engine ECU. 13.5-14.5 V with the engine running.",
    "engine.generator_duty": "How hard the alternator is working (DFM signal).",
    "vehicle.speed": "Road speed.",
    "engine.selected_gear": "Gear the engine ECU thinks is selected.",
    "engine.rpm.specified": "Target idle speed.",
    "engine.time_since_start": "Seconds since the engine was started.",
    "engine.camshaft.intake.bank1.specified": "Intake camshaft timing the ECU is asking for.",
    "engine.camshaft.intake.bank1.actual": "Measured intake camshaft timing. Should follow the requested value.",
    "engine.camshaft.intake.bank1.adjustment": "Intake camshaft adjustment.",
    "engine.misfire.count": "Misfires counted across all cylinders.",
    "engine.misfire.cylinder1.count": "Misfires counted on cylinder 1.",
    "engine.misfire.cylinder2.count": "Misfires counted on cylinder 2.",
    "engine.misfire.cylinder3.count": "Misfires counted on cylinder 3.",
    "engine.misfire.cylinder4.count": "Misfires counted on cylinder 4.",
    "engine.basic_setting.requirements": "Eight on/off flags showing which conditions for basic settings are met "
        "(1 = met).",
    "engine.ac.refrigerant_pressure": "A/C refrigerant pressure.",
    "engine.vehicle_mileage": "Odometer reading stored in the engine ECU.",
    # --- transmission (DSG) ---
    "transmission.fluid_temperature": "Transmission fluid temperature.",
    "transmission.clutch_oil_temperature": "Clutch oil temperature (G509). Rises with slipping and launches.",
    "transmission.module_temperature": "Transmission control module temperature.",
    "transmission.clutch1.specified_torque": "Torque clutch 1 is asked to carry.",
    "transmission.clutch2.specified_torque": "Torque clutch 2 is asked to carry.",
    "transmission.clutch1.actual_pressure": "Measured pressure on clutch 1 (G193).",
    "transmission.clutch2.actual_pressure": "Measured pressure on clutch 2 (G194).",
    "transmission.clutch1.shaft_speed": "Input shaft 1 speed (G501); compare with engine speed to see clutch slip.",
    "transmission.clutch2.shaft_speed": "Input shaft 2 speed (G502); compare with engine speed to see clutch slip.",
    # --- Haldex ---
    "awd.oil_temperature": "Haldex oil temperature.",
    "awd.plate_temperature": "Haldex clutch plate temperature; rises with sustained slip.",
    "awd.supply_voltage": "Battery voltage at the Haldex module.",
    "awd.oil_pressure": "Haldex clutch oil pressure: more pressure sends more torque to the rear.",
    "awd.estimated_torque": "Torque the Haldex module estimates it is sending to the rear axle.",
    "awd.valve.opening": "Opening of the Haldex clutch valve N273.",
    "awd.valve.current": "Current through the Haldex clutch valve N273.",
}


def _fallback(value):
    kind = value.get("kind")
    if kind == "status":
        return "A status code from the module. The logged number is a code, not a measurement; " \
               "a change means the state changed."
    if kind == "bitfield":
        return "Eight on/off flags. The log stores them as one number (the flags read as binary)."
    if kind == "string":
        return "Text from the module (e.g. identification). Not useful in a graph."
    unit = value.get("unit")
    return f"{value['name']}, as the module reports it" + (f" ({unit})." if unit else ".")


def describe(value) -> str:
    """Plain-words description of a catalog value (dict from flasher.measuring_catalog)."""
    return DESCRIPTIONS.get(value.get("id"), "") or _fallback(value)


def tooltip(value) -> str:
    from .channel_knowledge import note_for
    unit = f" · {value['unit']}" if value.get("unit") else ""
    note = note_for(cid=value.get("id"), name=value.get("name"))
    return f"<b>{escape(value['name'])}</b>{escape(unit)} · group {value['group']}.{value['field']}" \
           f"<br>{escape(describe(value))}" + (f"<br><i>Note: {escape(note)}</i>" if note else "")


# --- Integrated Engineering logging format help ------------------------------------

IE_GLOSSARY_URL = ("https://performancebyie.com/blogs/ie-auto-blog/"
                   "ie-powerlink-mobile-datalogging-variables-definitions?_pos=27&_sid=b9c91dc33&_ss=r")
IE_GLOSSARY_TITLE = "Variables Recorded During ECU Datalogging (With Definitions)"

# Copied word for word from the page above (their order, their typos), nothing added.
IE_GLOSSARY_NOTE = 'NOTE: Not all variables are available for all platforms and will vary by ECU and vehicle platform.'
IE_GLOSSARY = (
    ('Accelerator AV (%)', "The Accelerator Pedal Position, expressed as a percentage. It represents how much the accelerator pedal is depressed, indicating the driver's demand for engine power."),
    ('Air Mass (mg/stk)', 'The Mass Air Flow, measured in milligrams per stroke. It quantifies the amount of air entering the engine per combustion stroke.'),
    ('Boost AV (mBar)', 'Boost Pressure Actual Value in the Intake Manifold, expressed in millibar. Indicating the actual boost pressure measured in the intake manifold.'),
    ('Boost AV - CP (mBar)', 'Boost Pressure Actual Value in the Charge Pipe, expressed in millibar. Indicating the actual boost pressure measured in the charge pipe.'),
    ('Boost AV - IM (mBar)', 'Boost Pressure Actual Value in the Intake Manifold, expressed in millibar. Indicating the actual boost pressure measured in the intake manifold.'),
    ('Boost SP (mBar)', 'Boost Pressure Setpoint, expressed in millibar. Representing the desired boost pressure in the intake manifold.'),
    ('Boost SP - CP (mBar)', 'Boost Pressure Setpoint in the Charge Pipe, expressed in millibar. Representing the desired boost pressure in the charge pipe.'),
    ('Boost SP - IM (mBar)', 'Boost Pressure Setpoint in the Intake Manifold, expressed in millibar. Representing the desired boost pressure in the intake manifold.'),
    ('Bypass Valve (°)', 'Bypass Valve Position actual value, expressed in degrees. It represents the actual opening of the bypass valve.'),
    ('Coolant Temp (°C)', 'Engine Coolant Temperature, expressed in degrees Celsius. It represents the temperature of the engine coolant.'),
    ('DI (ms)', 'Direct Injection Pulse Width, expressed in milliseconds. Represents the duration of time the fuel injector is open during direct injection.'),
    ('DI PLS_1 (ms)', 'Direct Injection Pulse Width for Mode 1, expressed in milliseconds. Represents the duration of time the fuel injector is open during direct injection.'),
    ('DI PLS_1 (ms)', 'Direct Injection Pulse Width for Mode 2, expressed in milliseconds. Represents the duration of time the fuel injector is open during direct injection.'),
    ('DI PLS_1 (ms)', 'Direct Injection Pulse Width for Mode 3, expressed in milliseconds. Represents the duration of time the fuel injector is open during direct injection.'),
    ('Engine Speed (RPM)', "The rotational speed of the engine, measured in revolutions per minute. It indicates how fast the engine's crankshaft is rotating."),
    ('Ethanol (%)', 'Ethanol Content, expressed as a percentage. It represents the actual percentage of ethanol in the fuel, measured where the ethanol sensor is located. Vehicles equipped with IE TrueFlex only.'),
    ('HPFP AV (Bar)', 'High-Pressure Fuel Pump Actual Value, expressed in bar. Indicates the actual fuel pressure in the high-pressure fuel system.'),
    ('HPFP Dif (%)', 'Differential HPFP, expressed as a percentage. The difference between the HPFP setpoint and HPFP actual value.'),
    ('HPFP Duty (%)', 'High-Pressure Fuel Pump Duty Cycle, expressed as a percentage. It shows the duty cycle of the high-pressure fuel pump to maintain fuel pressure.'),
    ('HPFP SP (Bar)', 'High-Pressure Fuel Pump Setpoint, expressed in bar. Indicates the desired fuel pressure in the high-pressure fuel system.'),
    ('Ign Advance (°)', 'Ignition Advance. The number of degrees by which the ignition timing is advanced from its baseline value.'),
    ('Ign Cor 1-8 (°)', 'Ignition Timing Corrections for Cylinders 1 to 8, expressed in degrees. Shows adjustments to the ignition timing to optimize engine performance.'),
    ('Int Air Temp (°C)', 'Intake Air Temperature, measured in degrees Celsius. It indicates the temperature of the air measured in the engine’s intake manifold.'),
    ('Lambda AV', 'Lambda Actual Value, indicating the actual air-fuel ratio reference number.'),
    ('Lambda B1 AV', 'Lambda Actual Value for Bank 1. The actual air-fuel ratio reference for bank 1 of the engine.'),
    ('Lambda B1 AV', 'Lambda Actual Value for Bank 2. The actual air-fuel ratio reference for bank 1 of the engine, only used on vehicles with multiple engine banks.'),
    ('Lambda SP', "Lambda setpoint, a reference value for the air-fuel mixture's stoichiometry, i.e., the chemically balanced air-to-fuel ratio."),
    ('Load AV (%)', 'The engine Load Actual Value, expressed as a percentage. It represents the actual amount of work the engine is performing relative to its naturally aspirated maximum capacity. The value will return over 100% for forced induction systems.'),
    ('Load SP (%)', 'The engine Load Setpoint, expressed as a percentage. It represents the requested amount of work the engine is performing relative to its naturally aspirated maximum capacity. The value will return over 100% for forced induction systems.'),
    ('LPFP Duty (%)', 'Low-Pressure Fuel Pump Duty Cycle, expressed as a percentage. Represents the duty cycle of the low-pressure fuel pump.'),
    ('LPFP SP (Bar)', 'Low-Pressure Fuel Pump Setpoint, expressed in bar. Indicates the desired fuel pressure in the low-pressure fuel system.'),
    ('LTFT (%)', 'Long-Term Fuel Trim, expressed as a percentage. It represents long-term adjustments made to the fuel injection system to maintain the correct air-fuel ratio. Positive values indicate the system is adding fuel, while negative values indicate the system is reducing fuel.'),
    ('LTFT B1 (%)', 'Long-Term Fuel Trim for Bank 1'),
    ('LTFT B2 (%)', 'Long-Term Fuel Trim for Bank 2'),
    ('MPI (ms)', 'Multi-Port Injection, expressed in milliseconds. Represents the time duration the multi-port fuel injectors are open during direct injection. Unused variable unless the vehicle is equipped with MPI.'),
    ('N75 Duty (%)', 'N75 Duty Cycle, expressed as a percentage. It shows the duty cycle of the N75 Valve, which regulates the boost.'),
    ('STFT (%)', 'Short-Term Fuel Trim, expressed as a percentage. It represents short-term adjustments made to the fuel injection system to maintain the correct air-fuel ratio. Positive values indicate the system is adding fuel, while negative values indicate the system is reducing fuel.'),
    ('STFT B1 (%)', 'Short-Term Fuel Trim for Bank 1'),
    ('STFT B2 (%)', 'Short-Term Fuel Trim for Bank 2'),
    ('Time (ms)', 'The timestamp or time in milliseconds when the data was recorded.'),
    ('Throttle AV (%)', 'The Throttle Valve Position Actual Value, expressed as a percentage. It represents the actual opening of the throttle valve, controlling the airflow into the engine.'),
    ('Throttle SP (%)', 'The Throttle Valve Position Setpoint, expressed as a percentage. It represents the requested opening of the throttle valve, controlling the airflow into the engine.'),
    ('Torque (Nm)', 'Modeled engine torque, calculated value used to inform various drivetrain control units of how much expected torque is being generated, expressed in Newton meters. Not an actual torque measurement.'),
    ('Vehicle Speed (mph)', 'Vehicle Speed, expressed in miles per hour. It shows the speed of the vehicle.'),
    ('Wastegate AV (%)', 'Wastegate Actual Value, expressed as a percentage. Represents the actual opening of the wastegate valve to regulate turbocharger boost pressure.'),
    ('Wastegate SP (%)', 'Wastegate Setpoint, expressed as a percentage. Represents the desired opening of the wastegate valve to regulate turbocharger boost pressure.'),
)


# What this app writes in each Integrated Engineering variable on this car, where that
# isn't what the variable's name says (proven against real POWERlink logs of this car;
# see log_export.IE_COLUMNS).
IE_MISMATCH = {
    "Fuel Rail Actual (mbar)": "Really the rail pressure <b>setpoint</b>, in Integrated Engineering's wrong "
        "scale (0.04×A×B). Idle setpoint 40 bar shows as 96.",
    "LPFP Duty (mbar)": "Really the <b>actual rail pressure</b>, in Integrated Engineering's wrong scale, so it "
        "jumps around.",
    "Fuel Rail Spec (%)": "Really the <b>high-pressure pump duty</b> (N276).",
    "Fuel Pump Shut-Off Time (C)": "Really the <b>fuel temperature</b> (°C).",
    "Intervention (Bar)": "Really the <b>transmission intervention status</b>, a code, not a pressure.",
    "Lambda Control Value Bank 2 ()": "Really the <b>basic setting flags</b> (e.g. 11110111).",
}


# Glossary variable -> the column(s) POWERlink writes for it on this car, matched by name.
# Glossary variables not listed here aren't in the format on this car.
IE_GLOSSARY_COLUMNS = {
    "Boost AV (mBar)": ["Actual Boost (mbar)"],
    "Boost SP (mBar)": ["Requested Boost (mbar)"],
    "Coolant Temp (°C)": ["Coolant Temperature (°C)"],
    "DI (ms)": ["Injection Time (ms)"],
    "Engine Speed (RPM)": ["Engine Speed (rpm)"],
    "HPFP AV (Bar)": ["Fuel Rail Actual (mbar)"],
    "HPFP SP (Bar)": ["Fuel Rail Spec (%)"],
    "Ign Advance (°)": ["Ignition Angle (°)"],
    "Ign Cor 1-8 (°)": [f"Ignition Correction Cyl {n} (Deg)" for n in (1, 2, 3, 4)],
    "Int Air Temp (°C)": ["Intake Air Temperature (°C)"],
    "Load AV (%)": ["Actual Load (%)"],
    "Load SP (%)": ["Specified Load (%)"],
    "LPFP Duty (%)": ["LPFP Duty (mbar)"],
    "N75 Duty (%)": ["N75 Duty (%)"],
    "STFT B1 (%)": ["Lambda Control Value Bank 1 (%)"],
    "STFT B2 (%)": ["Lambda Control Value Bank 2 ()"],
    "Time (ms)": ["Time (min:sec.ms)"],
    "Throttle AV (%)": ["Throttle Valve Angle (%)"],
    "Torque (Nm)": ["Engine Moment (Nm)"],
}


def ie_help_html() -> str:
    from flasher.measuring_catalog import all_values
    from .log_export import IE_COLUMNS
    by_name = {v["name"]: v for v in all_values() if v["module"] == 0x01}
    source = {}  # POWERlink column -> our ECU value name (first source wins)
    for ours, header, _kind in IE_COLUMNS:
        source.setdefault(header, ours)

    def group_of(header):
        value = by_name.get(source.get(header, ""))
        return f"{value['group']}.{value['field']}" if value else ""

    rows = []
    for name, text in IE_GLOSSARY:
        groups = [g for g in (group_of(h) for h in IE_GLOSSARY_COLUMNS.get(name, [])) if g]
        if len(groups) > 2:
            groups = [f"{groups[0]}–{groups[-1]}"]  # Ign Cor: 20.1–20.4
        group = ", ".join(groups) or "–"
        rows.append(f"<tr><td><b>{escape(name)}</b></td><td>{group}</td><td>{escape(text)}</td></tr>")
    return f"""
<p>The Integrated Engineering logging format makes a copy of your log that looks like one from
Integrated Engineering's POWERlink logger: same variable names, order, time format (min:sec.ms) and
rounding. To use it, load the <b>Integrated Engineering Logger</b> preset on the Logger tab; that also
makes <b>Export…</b> start on Integrated Engineering CSV. The recorded log itself keeps the ECU's own names
and correct values. You can export any log in this format from the Logger or Analyze → <b>Export…</b>.</p>
<h3><a href="{IE_GLOSSARY_URL}">{escape(IE_GLOSSARY_TITLE)}</a></h3>
<p>{escape(IE_GLOSSARY_NOTE)}</p>
<table cellpadding="4" cellspacing="0" border="1" style="border-collapse:collapse">
<tr><th align="left">Variable</th><th align="left">ECU Group</th><th align="left">Definition</th></tr>
{''.join(rows)}
</table>
<p>ECU Group is the measuring group and field (group.field) read for that variable on this car; – means
it isn't in this format.</p>
"""

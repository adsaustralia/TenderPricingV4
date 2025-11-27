import streamlit as st
import pandas as pd
import numpy as np
import re
import json
from io import BytesIO
from openpyxl import load_workbook

st.set_page_config(page_title="Tender Pricing App V4", layout="wide")

# ---------- Defaults meta (for colouring old vs new groups) ----------
DEFAULT_GROUP_NAMES = {
    "CORFLUTE_3MM",
    "SCREENBOARD_2MM",
    "POSTER_BOARD",
    "WINDOW_SUPERCLING",
    "BANNER_SYNTHETIC",
    "FERROUS",
    "VINYL_MPI1105",
    "VINYL_3M7725",
    "VINYL_ARLON8000",
    "BACKLIT_DURATRAN",
    "BRAILLE_SIGNS",
}

# ---------- Helpers ----------

def num_to_col_letters(n: int) -> str:
    """1 -> A, 2 -> B, ... 27 -> AA, etc."""
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def to_excel_view(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build a view that looks like Excel:
    - Column headers: A, B, C...
    - Row numbers: 1, 2, 3...
    - Row 1 contains the original header names.
    """
    letters = [num_to_col_letters(i + 1) for i in range(len(df.columns))]
    data = [list(df.columns)] + df.astype(object).values.tolist()
    excel_df = pd.DataFrame(data, columns=letters)
    excel_df.index = range(1, len(excel_df) + 1)
    excel_df.index.name = ""
    return excel_df


def parse_dimension_to_sqm(dim_str: str) -> float:
    """
    Parse strings like '841mm x 1189mm', '594 x 841mm', '1.2m x 2m' to sqm.
    Assumptions:
    - mm, cm, m supported
    - if no unit, assume mm
    """
    if pd.isna(dim_str):
        return np.nan

    s = str(dim_str).lower().replace("×", "x")
    matches = re.findall(r"(\\d+(?:\\.\\d+)?)\\s*(mm|cm|m)?", s)
    if len(matches) < 2:
        return np.nan

    def to_m(val, unit):
        val = float(val)
        if unit == "cm":
            return val / 100.0
        if unit == "m":
            return val
        return val / 1000.0  # default or mm

    (v1, u1) = (matches[0][0], matches[0][1])
    (v2, u2) = (matches[1][0], matches[1][1])
    w = to_m(v1, u1)
    h = to_m(v2, u2)
    return w * h


def detect_side(text, ds_synonyms, ss_synonyms, default="SS"):
    if pd.isna(text):
        return default
    s = str(text).strip().lower()
    if any(tok in s for tok in ds_synonyms):
        return "DS"
    if any(tok in s for tok in ss_synonyms):
        return "SS"
    return default


def build_items_from_rows(
    df,
    col_letters_map,
    size_col_letter,
    material_col_letter,
    qty_annum_col_letter,
    qty_run_col_letter,
    runs_pa_col_letter,
    side_mode,
    side_col_letter,
    side_source_letter,
    ds_synonyms,
    ss_synonyms,
):
    """Items are in rows (BP-style)."""
    letter_to_header = col_letters_map
    result_rows = []

    size_col = letter_to_header.get(size_col_letter)
    mat_col = letter_to_header.get(material_col_letter) if material_col_letter else None
    qty_annum_col = letter_to_header.get(qty_annum_col_letter) if qty_annum_col_letter else None
    qty_run_col = letter_to_header.get(qty_run_col_letter) if qty_run_col_letter else None
    runs_pa_col = letter_to_header.get(runs_pa_col_letter) if runs_pa_col_letter else None

    if side_mode == "Separate column" and side_col_letter:
        side_col = letter_to_header.get(side_col_letter)
    else:
        side_col = None
    if side_mode == "Embedded in another column" and side_source_letter:
        side_src_col = letter_to_header.get(side_source_letter)
    else:
        side_src_col = None

    for idx, row in df.iterrows():
        size_val = row[size_col] if size_col else None
        material_val = row[mat_col] if mat_col else None

        qty_annum = pd.to_numeric(row[qty_annum_col], errors="coerce") if qty_annum_col else np.nan
        if qty_run_col:
            qty_run = pd.to_numeric(row[qty_run_col], errors="coerce")
        elif runs_pa_col and qty_annum_col:
            runs_pa = pd.to_numeric(row[runs_pa_col], errors="coerce")
            if not np.isnan(qty_annum) and not np.isnan(runs_pa) and runs_pa > 0:
                qty_run = qty_annum / runs_pa
            else:
                qty_run = np.nan
        else:
            qty_run = np.nan

        if side_mode == "Separate column" and side_col:
            side_raw = row[side_col]
        elif side_mode == "Embedded in another column" and side_src_col:
            side_raw = row[side_src_col]
        else:
            side_raw = None

        side = detect_side(side_raw, ds_synonyms, ss_synonyms, default="SS")
        sqm_per_unit = parse_dimension_to_sqm(size_val)
        sqm_per_annum = sqm_per_unit * qty_annum if (not np.isnan(sqm_per_unit) and not np.isnan(qty_annum)) else np.nan
        sqm_per_run = sqm_per_unit * qty_run if (not np.isnan(sqm_per_unit) and not np.isnan(qty_run)) else np.nan

        result_rows.append(
            {
                "Source Row": idx + 2,
                "Size": size_val,
                "Material": material_val,
                "Qty per annum": qty_annum,
                "Qty per run": qty_run,
                "Side": side,
                "SQM per unit": sqm_per_unit,
                "SQM per annum": sqm_per_annum,
                "SQM per run": sqm_per_run,
            }
        )

    return pd.DataFrame(result_rows)


def build_items_from_columns(
    df,
    size_row,
    material_row,
    qty_annum_row,
    qty_run_row,
    runs_pa_row,
    side_mode,
    side_row,
    side_source_row,
    ds_synonyms,
    ss_synonyms,
):
    """Items are in columns (Foot Locker-style)."""
    max_row, max_col = df.shape
    result_rows = []

    def excel_to_df_row(excel_row):
        return excel_row - 2

    size_r = excel_to_df_row(size_row) if size_row else None
    mat_r = excel_to_df_row(material_row) if material_row else None
    qty_annum_r = excel_to_df_row(qty_annum_row) if qty_annum_row else None
    qty_run_r = excel_to_df_row(qty_run_row) if qty_run_row else None
    runs_pa_r = excel_to_df_row(runs_pa_row) if runs_pa_row else None

    side_r = excel_to_df_row(side_row) if (side_mode == "Separate row" and side_row) else None
    side_src_r = excel_to_df_row(side_source_row) if (side_mode == "Embedded in another row" and side_source_row) else None

    for col_idx in range(max_col):
        col_letter = num_to_col_letters(col_idx + 1)

        size_val = df.iloc[size_r, col_idx] if size_r is not None else None
        material_val = df.iloc[mat_r, col_idx] if mat_r is not None else None

        qty_annum = pd.to_numeric(df.iloc[qty_annum_r, col_idx], errors="coerce") if qty_annum_r is not None else np.nan
        if qty_run_r is not None:
            qty_run = pd.to_numeric(df.iloc[qty_run_r, col_idx], errors="coerce")
        elif runs_pa_r is not None and qty_annum_r is not None:
            runs_pa = pd.to_numeric(df.iloc[runs_pa_r, col_idx], errors="coerce")
            if not np.isnan(qty_annum) and not np.isnan(runs_pa) and runs_pa > 0:
                qty_run = qty_annum / runs_pa
            else:
                qty_run = np.nan
        else:
            qty_run = np.nan

        if (
            pd.isna(size_val)
            and pd.isna(material_val)
            and np.isnan(qty_annum)
            and np.isnan(qty_run)
        ):
            continue

        if side_mode == "Separate row" and side_r is not None:
            side_raw = df.iloc[side_r, col_idx]
        elif side_mode == "Embedded in another row" and side_src_r is not None:
            side_raw = df.iloc[side_src_r, col_idx]
        else:
            side_raw = None

        side = detect_side(side_raw, ds_synonyms, ss_synonyms, default="SS")
        sqm_per_unit = parse_dimension_to_sqm(size_val)
        sqm_per_annum = sqm_per_unit * qty_annum if (not np.isnan(sqm_per_unit) and not np.isnan(qty_annum)) else np.nan
        sqm_per_run = sqm_per_unit * qty_run if (not np.isnan(sqm_per_unit) and not np.isnan(qty_run)) else np.nan

        result_rows.append(
            {
                "Source Column": col_letter,
                "Size": size_val,
                "Material": material_val,
                "Qty per annum": qty_annum,
                "Qty per run": qty_run,
                "Side": side,
                "SQM per unit": sqm_per_unit,
                "SQM per annum": sqm_per_annum,
                "SQM per run": sqm_per_run,
            }
        )

    return pd.DataFrame(result_rows)


# ---------- Session init ----------

if "group_assignments" not in st.session_state:
    st.session_state["group_assignments"] = {}
if "group_prices" not in st.session_state:
    st.session_state["group_prices"] = {}
if "group_volume_flags" not in st.session_state:
    st.session_state["group_volume_flags"] = {}
if "calc_df" not in st.session_state:
    st.session_state["calc_df"] = None
if "ds_syn_input" not in st.session_state:
    st.session_state["ds_syn_input"] = "ds,double sided,double-sided,2s,2 sided,2sided,double"
if "ss_syn_input" not in st.session_state:
    st.session_state["ss_syn_input"] = "ss,single sided,single-sided,1s,1 sided,1sided,single"
if "double_sided_loading_percent" not in st.session_state:
    st.session_state["double_sided_loading_percent"] = 25.0
if "hidden_cols_letters" not in st.session_state:
    st.session_state["hidden_cols_letters"] = []
if "hidden_rows_numbers" not in st.session_state:
    st.session_state["hidden_rows_numbers"] = []
if "tier_count" not in st.session_state:
    st.session_state["tier_count"] = 3
if "tier_thresholds" not in st.session_state:
    st.session_state["tier_thresholds"] = [250.0, 500.0, 1000.0]
if "tier_discounts" not in st.session_state:
    st.session_state["tier_discounts"] = [0.0, -1.0, -2.0]

# load default preset if exists
try:
    if not st.session_state["group_assignments"] and not st.session_state["group_prices"]:
        with open("material_groups_default.json", "r", encoding="utf-8") as f:
            preset = json.load(f)
        st.session_state["group_assignments"] = preset.get("group_assignments", {})
        st.session_state["group_prices"] = preset.get("group_prices", {})
        st.session_state["group_volume_flags"] = preset.get("group_volume_flags", {})
except Exception:
    pass


st.title("Tender Pricing App V4 (Interactive tiers)")

st.markdown(
    """
**Step 1:** Upload Excel and view all rows/columns (Excel-style A,B,C + 1,2,3)  
**Step 2:** Hide/Unhide rows & columns (without deleting)  
**Step 3:** Map fields (Size, Material, Qty, DS/SS) and calculate SQM + **Grouped Prices with optional volume tiers**
"""
)

uploaded_file = st.file_uploader("Upload Excel file", type=["xlsx", "xls"])
if uploaded_file is None:
    st.stop()

file_bytes = uploaded_file.getvalue()
excel_file = pd.ExcelFile(BytesIO(file_bytes))
sheet_name = st.selectbox("Select sheet", options=excel_file.sheet_names)

df = pd.read_excel(BytesIO(file_bytes), sheet_name=sheet_name)

st.subheader(f"Sheet preview (Excel-style): {sheet_name}")
excel_view = to_excel_view(df)
st.dataframe(excel_view)

# column letter mapping
col_letters = {}
col_labels = {}
for i, col_name in enumerate(df.columns):
    letter = num_to_col_letters(i + 1)
    col_letters[letter] = col_name
    col_labels[letter] = f"{letter} - {col_name}"


def select_letter(label, options_letters, default_letter=None, key=None, allow_none=False, none_label="(none)"):
    options = []
    mapping = {}
    if allow_none:
        options.append(none_label)
        mapping[none_label] = None
    for ltr in options_letters:
        lab = col_labels[ltr]
        options.append(lab)
        mapping[lab] = ltr
    if default_letter is not None and default_letter in options_letters:
        default_label = col_labels[default_letter]
        default_index = options.index(default_label)
    else:
        default_index = 0
    choice = st.selectbox(label, options=options, index=default_index, key=key)
    return mapping[choice]


# ---------- Hide / Unhide ----------

st.header("Step 2 – Hide / Unhide Rows & Columns")

all_letters = list(col_letters.keys())
col_options_labels = [col_labels[l] for l in all_letters]

default_cols_labels = [col_labels[l] for l in st.session_state["hidden_cols_letters"] if l in col_labels]

cols_to_hide_labels = st.multiselect(
    "Select columns to HIDE (by Excel letter):",
    options=col_options_labels,
    default=default_cols_labels,
)
cols_to_hide_letters = [lab.split(" - ")[0] for lab in cols_to_hide_labels]
st.session_state["hidden_cols_letters"] = cols_to_hide_letters

max_row = len(df) + 1
row_numbers = list(range(1, max_row + 1))
rows_to_hide_display = st.multiselect(
    "Select rows to HIDE (by Excel row number):",
    options=row_numbers,
    default=st.session_state["hidden_rows_numbers"],
)
st.session_state["hidden_rows_numbers"] = rows_to_hide_display

preview_excel_view = excel_view.copy()
if cols_to_hide_letters:
    preview_excel_view = preview_excel_view.drop(columns=cols_to_hide_letters)
if rows_to_hide_display:
    preview_excel_view = preview_excel_view.drop(index=rows_to_hide_display)

st.subheader("Preview with hidden rows/columns")
st.dataframe(preview_excel_view)

if st.button("Prepare file with hidden rows/columns"):
    wb = load_workbook(BytesIO(file_bytes))
    ws = wb[sheet_name]
    for letter in cols_to_hide_letters:
        ws.column_dimensions[letter].hidden = True
    for r in rows_to_hide_display:
        ws.row_dimensions[r].hidden = True
    out_buf = BytesIO()
    wb.save(out_buf)
    out_buf.seek(0)
    st.download_button(
        "Download workbook (with hidden rows/columns)",
        data=out_buf,
        file_name=f"{sheet_name}_hidden.xlsx",
    )

# ---------- SQM & Price Calculation ----------

st.header("Step 3 – SQM & Price Calculation")

layout_type = st.radio(
    "How are items laid out in this sheet?",
    ["Items are in rows (BP-style)", "Items are in columns (Foot Locker-style)"],
)

st.subheader("Double-sided / Single-sided configuration")
ds_syn_input = st.text_input("Values meaning DOUBLE-SIDED (comma-separated)", value=st.session_state["ds_syn_input"])
ss_syn_input = st.text_input("Values meaning SINGLE-SIDED (comma-separated)", value=st.session_state["ss_syn_input"])

ds_synonyms = [s.strip().lower() for s in ds_syn_input.split(",") if s.strip()]
ss_synonyms = [s.strip().lower() for s in ss_syn_input.split(",") if s.strip()]

double_sided_loading_percent = st.number_input(
    "Double-sided loading % (e.g. 25 for 25% extra over single-sided)",
    min_value=0.0,
    max_value=500.0,
    value=st.session_state["double_sided_loading_percent"],
    step=1.0,
)
st.session_state["double_sided_loading_percent"] = double_sided_loading_percent

calc_df = st.session_state["calc_df"]

if layout_type == "Items are in rows (BP-style)":
    st.subheader("Mapping (items in rows)")
    letters = list(col_letters.keys())

    headers_lower = {ltr: str(h).lower() for ltr, h in col_letters.items()}

    def guess_letter(substrings, fallback):
        for ltr, h in headers_lower.items():
            if any(sub in h for sub in substrings):
                return ltr
        return fallback

    size_default = guess_letter(["dim", "size"], letters[0] if letters else None)
    material_default = guess_letter(["material", "stock", "substrate"], letters[0] if letters else None)
    qty_annum_default = guess_letter(["annual", "per annum", "total annual volume", "pa"], letters[0] if letters else None)
    qty_run_default = guess_letter(["per run", "run qty", "run quantity"], letters[0] if letters else None)
    runs_pa_default = guess_letter(["runs p.a", "approx runs", "runs pa"], letters[0] if letters else None)

    size_col_letter = select_letter("Size / Dimensions column", letters, size_default, key="size_col_letter_rows")
    material_col_letter = select_letter("Material name column", letters, material_default, key="material_col_letter_rows", allow_none=True)
    qty_annum_col_letter = select_letter("Quantity PER ANNUM column", letters, qty_annum_default, key="qty_annum_col_letter_rows", allow_none=True)
    qty_run_col_letter = select_letter("Quantity PER RUN column (optional)", letters, qty_run_default, key="qty_run_col_letter_rows", allow_none=True)
    runs_pa_col_letter = select_letter("Runs PER ANNUM column (optional)", letters, runs_pa_default, key="runs_pa_col_letter_rows", allow_none=True)

    side_mode = st.selectbox("Where is DS/SS stored?", ["Separate column", "Embedded in another column", "Not available (assume SS)"])
    side_col_letter = None
    side_source_letter = None
    if side_mode == "Separate column":
        side_col_letter = select_letter("Column with DS/SS values", letters, key="side_col_letter_rows")
    elif side_mode == "Embedded in another column":
        side_source_letter = select_letter("Column where DS/SS text appears", letters, size_col_letter, key="side_source_letter_rows")

    if st.button("Calculate SQM & build item table", key="calc_rows"):
        calc_df = build_items_from_rows(
            df,
            col_letters,
            size_col_letter,
            material_col_letter,
            qty_annum_col_letter,
            qty_run_col_letter,
            runs_pa_col_letter,
            side_mode,
            side_col_letter,
            side_source_letter,
            ds_synonyms,
            ss_synonyms,
        )
        st.session_state["calc_df"] = calc_df

else:
    st.subheader("Mapping (items in columns)")
    max_row, max_col = df.shape
    row_options = list(range(2, max_row + 2))

    size_row = st.selectbox("Row with Size / Dimensions", options=row_options, key="size_row_cols")
    material_row = st.selectbox("Row with Material name", options=["(none)"] + row_options, key="material_row_cols")
    qty_annum_row = st.selectbox("Row with Quantity PER ANNUM", options=["(none)"] + row_options, key="qty_annum_row_cols")
    qty_run_row = st.selectbox("Row with Quantity PER RUN", options=["(none)"] + row_options, key="qty_run_row_cols")
    runs_pa_row = st.selectbox("Row with Runs PER ANNUM (optional)", options=["(none)"] + row_options, key="runs_pa_row_cols")

    material_row = None if material_row == "(none)" else material_row
    qty_annum_row = None if qty_annum_row == "(none)" else qty_annum_row
    qty_run_row = None if qty_run_row == "(none)" else qty_run_row
    runs_pa_row = None if runs_pa_row == "(none)" else runs_pa_row

    side_mode = st.selectbox("Where is DS/SS stored?", ["Separate row", "Embedded in another row", "Not available (assume SS)"])
    side_row = None
    side_source_row = None
    if side_mode == "Separate row":
        side_row = st.selectbox("Row with DS/SS values", options=row_options, key="side_row_cols")
    elif side_mode == "Embedded in another row":
        side_source_row = st.selectbox("Row where DS/SS text appears", options=row_options, key="side_source_row_cols")

    if st.button("Calculate SQM & build item table", key="calc_cols"):
        calc_df = build_items_from_columns(
            df,
            size_row,
            material_row,
            qty_annum_row,
            qty_run_row,
            runs_pa_row,
            side_mode,
            side_row,
            side_source_row,
            ds_synonyms,
            ss_synonyms,
        )
        st.session_state["calc_df"] = calc_df


# ---------- Pricing & Groups ----------

if st.session_state["calc_df"] is not None:
    calc_df = st.session_state["calc_df"].copy()

    st.subheader("Calculated SQM table (before pricing)")
    for col in ["Qty per annum", "Qty per run", "SQM per unit", "SQM per annum", "SQM per run"]:
        if col in calc_df.columns:
            calc_df[col] = calc_df[col].round(2)
    st.dataframe(calc_df)

    st.subheader("Material Groups & Pricing")

    materials = sorted({m for m in calc_df.get("Material", pd.Series()).dropna().unique()})
    group_assignments = st.session_state["group_assignments"]
    group_prices = st.session_state["group_prices"]
    group_volume_flags = st.session_state["group_volume_flags"]

    st.markdown("**Step 1 – Assign materials to groups**")

    if "existing_group_choice" not in st.session_state:
        st.session_state["existing_group_choice"] = "SelectExisting/None"
    if "group_name_input" not in st.session_state:
        st.session_state["group_name_input"] = ""

    existing_groups = sorted(set(group_assignments.values()) | set(group_prices.keys()))
    unassigned_materials = [m for m in materials if m not in group_assignments]

    with st.form("assign_group_form"):
        selected_materials = st.multiselect(
            "Select material(s) to assign to a group (only unassigned shown)",
            options=unassigned_materials,
        )
        col1, col2 = st.columns(2)
        with col1:
            existing_group_choice = st.selectbox(
                "Pick existing group (optional)",
                options=["SelectExisting/None"] + existing_groups,
                key="existing_group_choice",
            )
        with col2:
            group_name_input = st.text_input("Or type new group name", key="group_name_input")

        submitted_assign = st.form_submit_button("Apply group to selected materials")

    if submitted_assign:
        manual = group_name_input.strip()
        if manual:
            group_name = manual
        elif existing_group_choice != "SelectExisting/None":
            group_name = existing_group_choice
        else:
            group_name = ""
        if not group_name:
            st.warning("Please choose or type a group name.")
        elif not selected_materials:
            st.warning("Please select at least one material.")
        else:
            for m in selected_materials:
                group_assignments[m] = group_name
            if group_name not in group_volume_flags:
                group_volume_flags[group_name] = True
            st.session_state["group_assignments"] = group_assignments
            st.session_state["group_volume_flags"] = group_volume_flags
            st.success(f"Assigned group '{group_name}' to {len(selected_materials)} material(s).")

    mapping_df = pd.DataFrame({"Material": materials, "Group": [group_assignments.get(m, "") for m in materials]})
    st.markdown("**Current material → group mapping (editable)**")
    edited_mapping_df = st.data_editor(mapping_df, num_rows="fixed", use_container_width=True, disabled=["Material"])
    if st.button("Apply changes from mapping table"):
        new_assignments = {}
        for _, row_m in edited_mapping_df.iterrows():
            m = row_m["Material"]
            g = str(row_m["Group"]).strip()
            if g:
                new_assignments[m] = g
        st.session_state["group_assignments"] = new_assignments
        group_assignments = new_assignments
        st.success("Updated group assignments from table.")

    group_assignments = st.session_state["group_assignments"]
    group_prices = st.session_state["group_prices"]
    group_volume_flags = st.session_state["group_volume_flags"]

    st.markdown("**Step 2 – Set group BASE prices (per SQM, AUD) and volume toggle**")

    all_groups = sorted(set(group_assignments.values()) | set(group_prices.keys()))
    used_groups = set(group_assignments.values())
    not_relevant_groups = sorted(all_groups - used_groups)

    extra_groups_to_show = st.multiselect(
        "Also show these groups (not used in this campaign):",
        options=not_relevant_groups,
    )

    visible_groups = sorted(list(used_groups | set(extra_groups_to_show)))

    new_group_prices = {}
    new_group_volume_flags = {}

    if visible_groups:
        cols_per_row = 4
        for i, g in enumerate(visible_groups):
            if i % cols_per_row == 0:
                cols = st.columns(cols_per_row)
            col = cols[i % cols_per_row]
            with col:
                is_default = g in DEFAULT_GROUP_NAMES
                caption_color = "green" if is_default else "red"
                st.markdown(
                    f"<div style='color:{caption_color}; font-weight:600; font-size:0.85rem;'>"
                    f"BASE price per SQM (AUD) for group '{g}'"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                init_price = float(group_prices.get(g, 0.0) or 0.0)
                price_val = st.number_input("", min_value=0.0, step=0.01, format="%.2f", value=init_price, key=f"price_{g}")
                use_tiers = st.checkbox("Use volume tiers", value=group_volume_flags.get(g, True), key=f"use_tiers_{g}")
            new_group_prices[g] = price_val if price_val > 0 else np.nan
            new_group_volume_flags[g] = use_tiers

    for g in all_groups:
        if g not in visible_groups:
            new_group_prices[g] = group_prices.get(g, np.nan)
            new_group_volume_flags[g] = group_volume_flags.get(g, True)

    st.session_state["group_prices"] = new_group_prices
    st.session_state["group_volume_flags"] = new_group_volume_flags
    group_prices = new_group_prices
    group_volume_flags = new_group_volume_flags

    # ---------- Volume-based tiers (interactive table) ----------

    st.markdown("**Volume-based price breaks (by SQM per annum, global)**")
    st.caption(
        "Configure SQM tiers and % adjustments vs BASE price. Each group can opt in/out."
    )

    tier_count = st.number_input(
        "Number of SQM tiers (ranges)",
        min_value=1,
        max_value=10,
        step=1,
        value=st.session_state["tier_count"],
    )
    tier_count = int(tier_count)
    st.session_state["tier_count"] = tier_count

    while len(st.session_state["tier_thresholds"]) < tier_count:
        st.session_state["tier_thresholds"].append(st.session_state["tier_thresholds"][-1] if st.session_state["tier_thresholds"] else 250.0)
    while len(st.session_state["tier_discounts"]) < tier_count:
        st.session_state["tier_discounts"].append(0.0)
    st.session_state["tier_thresholds"] = st.session_state["tier_thresholds"][:tier_count]
    st.session_state["tier_discounts"] = st.session_state["tier_discounts"][:tier_count]

    tier_rows = []
    for i in range(tier_count):
        tier_rows.append(
            {
                "Tier": i + 1,
                "Max SQM": float(st.session_state["tier_thresholds"][i]),
                "% vs base": float(st.session_state["tier_discounts"][i]),
            }
        )

    tier_df = pd.DataFrame(tier_rows)
    st.write("Configure tiers (edit cells below):")
    edited_tier_df = st.data_editor(tier_df, num_rows="fixed", use_container_width=True, disabled=["Tier"])

    tier_thresholds = []
    tier_discounts = []
    for _, r in edited_tier_df.iterrows():
        max_sqm = float(r["Max SQM"]) if not pd.isna(r["Max SQM"]) else 0.0
        if max_sqm < 0:
            max_sqm = 0.0
        tier_thresholds.append(max_sqm)
        disc = float(r["% vs base"]) if not pd.isna(r["% vs base"]) else 0.0
        tier_discounts.append(disc)

    st.session_state["tier_thresholds"] = tier_thresholds
    st.session_state["tier_discounts"] = tier_discounts

    tiers_sorted = sorted(zip(tier_thresholds, tier_discounts), key=lambda x: x[0])
    sorted_thresholds = [t[0] for t in tiers_sorted]
    sorted_discounts = [t[1] for t in tiers_sorted]

    def pick_discount_for_sqm(sqm):
        if pd.isna(sqm) or not sorted_thresholds:
            return 0.0
        for T, D in zip(sorted_thresholds, sorted_discounts):
            if sqm <= T:
                return D
        return sorted_discounts[-1]

    group_assignment_map = group_assignments
    group_price_map = group_prices

    calc_with_price = calc_df.copy()

    def resolve_base_price(material):
        g = group_assignment_map.get(material)
        if g:
            gp = group_price_map.get(g)
            if gp is not None and not pd.isna(gp):
                return gp
        return np.nan

    calc_with_price["Base Price per SQM (AUD)"] = calc_with_price.get("Material", pd.Series()).apply(resolve_base_price)

    volume_discounts = []
    tier_prices = []
    for _, row_p in calc_with_price.iterrows():
        base = row_p["Base Price per SQM (AUD)"]
        material = row_p.get("Material")
        if pd.isna(base):
            volume_discounts.append(0.0)
            tier_prices.append(np.nan)
            continue
        group = group_assignment_map.get(material)
        use_tiers = group_volume_flags.get(group, True)
        if not use_tiers:
            disc = 0.0
            tier_price = base
        else:
            sqm_metric = row_p.get("SQM per annum")
            if pd.isna(sqm_metric):
                sqm_metric = row_p.get("SQM per run")
            disc = pick_discount_for_sqm(sqm_metric)
            tier_price = base * (1.0 + disc / 100.0)
        volume_discounts.append(disc)
        tier_prices.append(tier_price)

    calc_with_price["Volume Discount %"] = volume_discounts
    calc_with_price["Tier Price per SQM (AUD)"] = tier_prices

    ds_factor = 1.0 + st.session_state["double_sided_loading_percent"] / 100.0
    calc_with_price["Effective Price per SQM (AUD)"] = calc_with_price.apply(
        lambda r: r["Tier Price per SQM (AUD)"] * ds_factor if r.get("Side") == "DS" else r["Tier Price per SQM (AUD)"],
        axis=1,
    )

    calc_with_price["Price per unit (AUD)"] = calc_with_price["SQM per unit"] * calc_with_price["Effective Price per SQM (AUD)"]
    calc_with_price["Price per annum (AUD)"] = calc_with_price["SQM per annum"] * calc_with_price["Effective Price per SQM (AUD)"]
    calc_with_price["Price per run (AUD)"] = calc_with_price["SQM per run"] * calc_with_price["Effective Price per SQM (AUD)"]

    for col in ["Price per unit (AUD)", "Price per annum (AUD)", "Price per run (AUD)", "Volume Discount %"]:
        if col in calc_with_price.columns:
            calc_with_price[col] = calc_with_price[col].round(2)

    if "Material" in calc_with_price.columns:
        calc_with_group = calc_with_price.copy()
        calc_with_group["Group"] = calc_with_group["Material"].map(group_assignment_map).fillna("")
        grouped_rows = calc_with_group[calc_with_group["Group"] != ""]
        if not grouped_rows.empty:
            agg = {}
            for c in ["SQM per unit", "SQM per annum", "SQM per run", "Price per annum (AUD)", "Price per run (AUD)"]:
                if c in grouped_rows.columns:
                    agg[c] = "sum"
            group_summary = grouped_rows.groupby("Group").agg(agg).reset_index()
            group_summary["Base Group Price per SQM (AUD)"] = group_summary["Group"].map(group_price_map).round(2)
            for c in group_summary.columns:
                if c != "Group":
                    group_summary[c] = group_summary[c].round(2)
            st.subheader("Square metres & prices by Group")
            st.dataframe(group_summary)

    display_df = calc_with_price.copy()
    for col in ["Price per unit (AUD)", "Price per annum (AUD)", "Price per run (AUD)"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(lambda x: "" if pd.isna(x) else f"${x:,.2f}")

    st.subheader("Final calculation table (with grouped pricing)")
    st.dataframe(display_df)

    out_calc = BytesIO()
    calc_with_price.to_excel(out_calc, index=False, sheet_name="CALC")
    out_calc.seek(0)
    st.download_button(
        "Download SQM & pricing table (CALC.xlsx)",
        data=out_calc,
        file_name="sqm_pricing_calc.xlsx",
    )


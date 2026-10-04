#!/usr/bin/env python3
"""
Somaiya Vidyavihar Squirrel Study - analysis script.

Usage:
    python analyze_squirrels.py KOBO_EXPORT.xlsx [--zones zone_areas.csv] [--out results]

Input : the XLS/XLSX exported from KoboToolbox (DATA > Downloads > XLS).
        First sheet = surveys, sheet "sightings" = one row per sighting.
Output: folder with clean tables, zone summary, QC report, QGIS-ready points, charts.

Counts are a SIGHTING INDEX (sightings per effort-hour), not a population estimate.
"""
import argparse, os, sys
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Rough campus bounding box (lat/lon). Points outside are flagged. Adjust after QGIS base map.
CAMPUS_BBOX = dict(lat_min=19.068, lat_max=19.082, lon_min=72.890, lon_max=72.908)
MAX_GPS_ACCURACY_M = 10
SLOT_ORDER = ["morning", "afternoon", "evening"]


def load(path):
    xl = pd.ExcelFile(path)
    surveys = xl.parse(xl.sheet_names[0])
    if "sightings" in xl.sheet_names:
        sightings = xl.parse("sightings")
    else:  # no sightings recorded yet
        sightings = pd.DataFrame(columns=["_parent_index", "squirrel_count"])
    return surveys, sightings


def build_tables(surveys, sightings, zones):
    s = surveys.copy()
    s["survey_id"] = s["_index"]
    s["date"] = pd.to_datetime(s["today"], errors="coerce")
    s["effort_hours"] = s["duration_min"] / 60
    for c in ("human_start", "human_mid", "human_end"):
        s[c] = pd.to_numeric(s[c], errors="coerce")
    s["human_avg"] = s[["human_start", "human_mid", "human_end"]].mean(axis=1)

    g = sightings.copy()
    g["squirrel_count"] = pd.to_numeric(g["squirrel_count"], errors="coerce")
    tot = g.groupby("_parent_index")["squirrel_count"].sum()
    n = g.groupby("_parent_index").size()
    s["total_squirrels"] = s["survey_id"].map(tot).fillna(0)
    s["n_sightings"] = s["survey_id"].map(n).fillna(0).astype(int)
    s["sightings_per_effort_hour"] = s["total_squirrels"] / s["effort_hours"]
    s = s.merge(zones[["zone_id", "area_ha"]], on="zone_id", how="left")
    s["squirrels_per_ha"] = s["total_squirrels"] / s["area_ha"]
    # ratio is undefined when no squirrels were seen
    s["student_squirrel_ratio"] = s["human_avg"] / s["sightings_per_effort_hour"].where(s["sightings_per_effort_hour"] > 0)
    keep = ["survey_id", "date", "observer_id", "zone_id", "time_slot", "day_type", "lecture_status", "weather",
            "duration_min", "human_start", "human_mid", "human_end", "human_avg", "n_sightings", "total_squirrels",
            "effort_hours", "sightings_per_effort_hour", "area_ha", "squirrels_per_ha", "student_squirrel_ratio"]
    return s[[c for c in keep if c in s.columns]], g


def summarize(df, by):
    out = df.groupby(by, observed=True).agg(
        surveys=("survey_id", "count"),
        effort_hours=("effort_hours", "sum"),
        total_squirrels=("total_squirrels", "sum"),
        avg_human_count=("human_avg", "mean"),
    ).reset_index()
    out["sightings_per_effort_hour"] = out["total_squirrels"] / out["effort_hours"]
    out["student_squirrel_ratio"] = out["avg_human_count"] / out["sightings_per_effort_hour"].where(out["sightings_per_effort_hour"] > 0)
    return out


def quality_checks(s, g):
    issues = []
    def add(kind, ident, msg): issues.append({"check": kind, "id": ident, "detail": msg})
    for _, r in s.iterrows():
        sid = r["survey_id"]
        if pd.isna(r["zone_id"]): add("missing", sid, "zone missing")
        if not (10 <= (r["duration_min"] or 0) <= 60): add("duration", sid, f"duration {r['duration_min']} min (expected ~30)")
        if r[["human_start", "human_mid", "human_end"]].isna().any(): add("missing", sid, "human count missing")
        if r["area_ha"] != r["area_ha"]: add("zone_area", sid, f"no area for zone {r['zone_id']} (squirrels_per_ha not calculated)")
    dup = s.duplicated(["zone_id", "date", "time_slot", "observer_id"], keep=False)
    for sid in s.loc[dup, "survey_id"]: add("duplicate", sid, "same zone/date/slot/observer appears more than once")
    c = CAMPUS_BBOX
    for i, r in g.iterrows():
        pid = r.get("_parent_index")
        lat, lon, acc = r.get("_location_latitude"), r.get("_location_longitude"), r.get("_location_precision")
        if pd.isna(lat) or pd.isna(lon): add("gps", pid, "sighting has no GPS point"); continue
        if not (c["lat_min"] <= lat <= c["lat_max"] and c["lon_min"] <= lon <= c["lon_max"]):
            add("gps_outside_campus", pid, f"point {lat:.5f},{lon:.5f} is outside the campus box")
        if pd.notna(acc) and acc > MAX_GPS_ACCURACY_M: add("gps_accuracy", pid, f"GPS accuracy {acc:.0f} m (> {MAX_GPS_ACCURACY_M} m)")
        if pd.notna(r.get("squirrel_count")) and r["squirrel_count"] > 15: add("count", pid, f"unusually large count {r['squirrel_count']:.0f}")
    # coverage: zone x slot
    return pd.DataFrame(issues, columns=["check", "id", "detail"])


def charts(s, zs, out):
    if s.empty: return
    def bar(df, x, y, title, fname, xlabel, ylabel):
        fig, ax = plt.subplots(figsize=(8, 4.5))
        ax.bar(df[x].astype(str), df[y].fillna(0), color="#1F4E78")
        ax.set_title(title); ax.set_xlabel(xlabel); ax.set_ylabel(ylabel)
        plt.setp(ax.get_xticklabels(), rotation=30, ha="right"); fig.tight_layout()
        fig.savefig(os.path.join(out, fname), dpi=150); plt.close(fig)
    bar(zs, "zone_id", "sightings_per_effort_hour", "Squirrel sightings per effort-hour by zone", "chart_zone_sightings.png", "Zone", "Sightings per effort-hour")
    ts = summarize(s, "time_slot"); ts["time_slot"] = pd.Categorical(ts["time_slot"], SLOT_ORDER, ordered=True); ts = ts.sort_values("time_slot")
    bar(ts, "time_slot", "sightings_per_effort_hour", "Squirrel sightings per effort-hour by time slot", "chart_time_slot.png", "Time slot", "Sightings per effort-hour")
    d = s.dropna(subset=["human_avg", "sightings_per_effort_hour"])
    if len(d) >= 2:
        fig, ax = plt.subplots(figsize=(6, 4.5))
        ax.scatter(d["human_avg"], d["sightings_per_effort_hour"], color="#C0504D")
        ax.set_xlabel("Average people in zone"); ax.set_ylabel("Sightings per effort-hour"); ax.set_title("Squirrel sightings vs human presence")
        fig.tight_layout(); fig.savefig(os.path.join(out, "chart_humans_vs_sightings.png"), dpi=150); plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export"); ap.add_argument("--zones", default=None); ap.add_argument("--out", default="results")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    zones = pd.read_csv(a.zones) if a.zones else pd.DataFrame({"zone_id": [], "area_m2": []})
    zones["area_ha"] = pd.to_numeric(zones.get("area_m2"), errors="coerce") / 10000
    surveys, sightings = load(a.export)
    s, g = build_tables(surveys, sightings, zones)
    zs = summarize(s, "zone_id").merge(zones[["zone_id", "area_ha"]], on="zone_id", how="left")
    zs["mean_squirrels_per_survey_per_ha"] = (zs["total_squirrels"] / zs["surveys"]) / zs["area_ha"]
    zs_slot = summarize(s, ["zone_id", "time_slot"])
    qc = quality_checks(s, g)
    s.to_csv(f"{a.out}/clean_surveys.csv", index=False)
    g.to_csv(f"{a.out}/clean_sightings.csv", index=False)
    zs.to_csv(f"{a.out}/zone_summary.csv", index=False)
    summarize(s, "time_slot").to_csv(f"{a.out}/time_slot_summary.csv", index=False)
    summarize(s, "day_type").to_csv(f"{a.out}/day_type_summary.csv", index=False)
    zs_slot.to_csv(f"{a.out}/coverage_zone_by_slot.csv", index=False)
    qc.to_csv(f"{a.out}/quality_report.csv", index=False)
    pts = g.merge(s[["survey_id", "zone_id", "time_slot", "day_type", "date"]], left_on="_parent_index", right_on="survey_id", how="left")
    pts[["_location_latitude", "_location_longitude", "squirrel_count", "species", "behavior", "habitat", "tree_species", "zone_id", "time_slot", "day_type", "date"]] \
        .rename(columns={"_location_latitude": "lat", "_location_longitude": "lon"}).to_csv(f"{a.out}/sightings_for_qgis.csv", index=False)
    charts(s, zs, a.out)
    print(f"Surveys: {len(s)} | Sightings rows: {len(g)} | Squirrels counted: {int(s['total_squirrels'].sum())}")
    print(f"Quality issues found: {len(qc)}")
    if len(qc): print(qc.to_string(index=False))
    print(f"Results written to: {a.out}/")


if __name__ == "__main__":
    main()

import pandas as pd

old = pd.read_csv('baseline_weekly.csv').set_index(['week','building']).sort_index()
new = pd.read_csv('new_weekly.csv').set_index(['week','building']).sort_index()
common = old.index.intersection(new.index)
cols = ['kWh','thermal_kWh','gas_therm','water_gallon','normalized_kWh']

old_c = old.loc[common, cols].astype(float)
new_c = new.loc[common, cols].astype(float)
diffs = (old_c - new_c).abs()
mask  = (diffs > 0.01).any(axis=1)

print('=== ROWS THAT DIFFER ===')
print(f'Total mismatched rows: {mask.sum()}')
print()
print('--- OLD values (baseline) ---')
print(old_c[mask].to_string())
print()
print('--- NEW values (from Hostinger) ---')
print(new_c[mask].to_string())
print()
print('--- Differences (old - new) ---')
print((old_c[mask] - new_c[mask]).to_string())
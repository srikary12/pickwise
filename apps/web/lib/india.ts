// SPDX-License-Identifier: AGPL-3.0-only
// States and union territories as ISO 3166-2:IN codes (what the API stores and PT/LWF key on).
export const INDIAN_STATES: readonly { value: string; label: string }[] = [
  ["IN-AN", "Andaman and Nicobar Islands"],
  ["IN-AP", "Andhra Pradesh"],
  ["IN-AR", "Arunachal Pradesh"],
  ["IN-AS", "Assam"],
  ["IN-BR", "Bihar"],
  ["IN-CH", "Chandigarh"],
  ["IN-CG", "Chhattisgarh"],
  ["IN-DH", "Dadra and Nagar Haveli and Daman and Diu"],
  ["IN-DL", "Delhi"],
  ["IN-GA", "Goa"],
  ["IN-GJ", "Gujarat"],
  ["IN-HR", "Haryana"],
  ["IN-HP", "Himachal Pradesh"],
  ["IN-JK", "Jammu and Kashmir"],
  ["IN-JH", "Jharkhand"],
  ["IN-KA", "Karnataka"],
  ["IN-KL", "Kerala"],
  ["IN-LA", "Ladakh"],
  ["IN-LD", "Lakshadweep"],
  ["IN-MP", "Madhya Pradesh"],
  ["IN-MH", "Maharashtra"],
  ["IN-MN", "Manipur"],
  ["IN-ML", "Meghalaya"],
  ["IN-MZ", "Mizoram"],
  ["IN-NL", "Nagaland"],
  ["IN-OD", "Odisha"],
  ["IN-PY", "Puducherry"],
  ["IN-PB", "Punjab"],
  ["IN-RJ", "Rajasthan"],
  ["IN-SK", "Sikkim"],
  ["IN-TN", "Tamil Nadu"],
  ["IN-TS", "Telangana"],
  ["IN-TR", "Tripura"],
  ["IN-UP", "Uttar Pradesh"],
  ["IN-UK", "Uttarakhand"],
  ["IN-WB", "West Bengal"],
].map(([value, label]) => ({ value: value as string, label: label as string }));

export function stateName(code: string): string {
  return INDIAN_STATES.find((state) => state.value === code)?.label ?? code;
}

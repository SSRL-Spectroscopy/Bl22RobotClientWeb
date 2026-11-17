using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;

/// <summary>
/// Intensity-weighted fluorescence energy (eV) for an element and absorption edge, read from
/// xray_fluorescence_lines.csv (X-Ray Data Booklet Tables 1-2 / 1-3, https://xdb.lbl.gov/).
/// CSV columns: Z,Element,Edge,Line,Transition,Energy_eV,RelInt,Source   (lines starting with '#' are comments)
///
/// Lines used per edge (lines that fill the vacancy from the next shell out):
///   K   -> Ka1, Ka2          L-3 -> La1, La2          L-2 -> Lb1          M-5 -> Ma1
/// </summary>
public static class XrayFluorescence
{
    private class Line { public string Name; public double Energy; public double Intensity; }

    private static readonly Dictionary<string, List<Line>> _table = new Dictionary<string, List<Line>>();
    private static readonly string[] Roman = { "I", "II", "III", "IV", "V", "VI", "VII" };

    /// <summary>Loads (or reloads) the fluorescence line table from the CSV file.</summary>
    public static void Load(string path)
    {
        _table.Clear();
        foreach (string raw in File.ReadLines(path))
        {
            string line = raw.Trim();
            if (line.Length == 0 || line.StartsWith("#") || line.StartsWith("Z,")) continue;
            List<string> f = SplitCsv(line);
            if (f.Count < 7) continue;
            string key = Key(f[1], f[2]);
            if (!_table.TryGetValue(key, out var list)) _table[key] = list = new List<Line>();
            list.Add(new Line
            {
                Name = f[3],
                Energy = double.Parse(f[5], CultureInfo.InvariantCulture),
                Intensity = double.Parse(f[6], CultureInfo.InvariantCulture)
            });
        }
    }

    /// <summary>
    /// Returns the intensity-weighted mean energy (eV) of the principal fluorescence lines
    /// that fill a vacancy in the given edge, e.g. GetEmissionEnergy("Fe", "K") = 6399.5
    /// (Ka1 6403.8 x 100, Ka2 6390.8 x 50). Element is case-insensitive; edge accepts
    /// "L-3", "L3", "L III", "K", "K-1". Throws KeyNotFoundException if not tabulated.
    /// </summary>
    public static double GetEmissionEnergy(string element, string edge)
    {
        if (TryGetEmissionEnergy(element, edge, out double e)) return e;
        throw new KeyNotFoundException($"No fluorescence lines tabulated for {element} {edge}.");
    }

    /// <summary>Non-throwing variant.</summary>
    public static bool TryGetEmissionEnergy(string element, string edge, out double energy)
    {
        if (_table.Count == 0) Load("xray_fluorescence_lines.csv");
        energy = double.NaN;
        if (!_table.TryGetValue(Key(element, edge), out var lines) || lines.Count == 0) return false;
        double sumEI = 0, sumI = 0;
        foreach (var l in lines) { sumEI += l.Energy * l.Intensity; sumI += l.Intensity; }
        energy = sumEI / sumI;
        return true;
    }

    private static string Key(string element, string edge) => NormElement(element) + "|" + NormEdge(edge);

    private static string NormElement(string el)
    {
        el = (el ?? "").Trim();
        if (el.Length == 0) throw new ArgumentException("Element is empty.");
        return char.ToUpperInvariant(el[0]) + el.Substring(1).ToLowerInvariant();
    }

    private static string NormEdge(string edge)
    {
        string s = (edge ?? "").Replace("-", "").Replace(" ", "").Replace("_", "").Trim().ToUpperInvariant();
        if (s.Length == 0) throw new ArgumentException("Edge is empty.");
        char shell = s[0];
        string sub = s.Substring(1);
        if (sub.Length > 0 && !char.IsDigit(sub[0]))            // Roman numerals: L-III -> L3
        {
            int idx = Array.IndexOf(Roman, sub);
            if (idx < 0) throw new ArgumentException($"Unrecognised edge '{edge}'.");
            sub = (idx + 1).ToString(CultureInfo.InvariantCulture);
        }
        if (shell == 'K' && (sub == "" || sub == "1")) return "K";
        return shell + "-" + sub;
    }

    // Minimal CSV splitter that honours double-quoted fields (line names like "Ka1,2").
    private static List<string> SplitCsv(string line)
    {
        var result = new List<string>();
        var sb = new StringBuilder();
        bool inQuotes = false;
        for (int i = 0; i < line.Length; i++)
        {
            char c = line[i];
            if (c == '"')
            {
                if (inQuotes && i + 1 < line.Length && line[i + 1] == '"') { sb.Append('"'); i++; }
                else inQuotes = !inQuotes;
            }
            else if (c == ',' && !inQuotes) { result.Add(sb.ToString()); sb.Clear(); }
            else sb.Append(c);
        }
        result.Add(sb.ToString());
        return result;
    }
}

// Example:
//   XrayFluorescence.Load(@"C:\data\xray_fluorescence_lines.csv");
//   double e = XrayFluorescence.GetEmissionEnergy("Fe", "K");     // 6399.47 eV
//   double l = XrayFluorescence.GetEmissionEnergy("Pb", "L-3");   // 10541.4 eV

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;

/// <summary>
/// Lookup of X-ray absorption edge energies (eV) read from xray_edges.csv
/// (X-Ray Data Booklet, Table 1-1, https://xdb.lbl.gov/).
/// CSV columns: Z,Element,Edge,Energy_eV,Notes   (lines starting with '#' are comments)
/// </summary>
public static class XrayEdges
{
    private static readonly Dictionary<string, double> _table = new Dictionary<string, double>();
    private static readonly string[] Roman = { "I", "II", "III", "IV", "V", "VI", "VII" };

    /// <summary>Loads (or reloads) the edge table from the CSV file.</summary>
    public static void Load(string path)
    {
        _table.Clear();
        foreach (string raw in File.ReadLines(path))
        {
            string line = raw.Trim();
            if (line.Length == 0 || line.StartsWith("#") || line.StartsWith("Z,")) continue;
            string[] f = line.Split(',');
            if (f.Length < 4) continue;
            double e = double.Parse(f[3], CultureInfo.InvariantCulture);
            _table[Key(f[1], f[2])] = e;
        }
    }

    /// <summary>
    /// Returns the edge energy in eV, e.g. GetEnergy("Fe", "L-3") = 706.8.
    /// Element is case-insensitive ("Fe", "fe", "FE"). Edge accepts "L-3", "L3", "l3", "L III", "L-III";
    /// "K" and "K-1" are equivalent. Throws KeyNotFoundException if the element/edge is not tabulated.
    /// </summary>
    public static double GetEnergy(string element, string edge)
    {
        if (_table.Count == 0) Load("xray_edges.csv");
        if (_table.TryGetValue(Key(element, edge), out double energy)) return energy;
        throw new KeyNotFoundException($"No tabulated edge '{edge}' for element '{element}'.");
    }

    /// <summary>Non-throwing variant.</summary>
    public static bool TryGetEnergy(string element, string edge, out double energy)
    {
        if (_table.Count == 0) Load("xray_edges.csv");
        return _table.TryGetValue(Key(element, edge), out energy);
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
}

// Example:
//   XrayEdges.Load(@"C:\data\xray_edges.csv");
//   double e = XrayEdges.GetEnergy("Fe", "L-3");   // 706.8 eV

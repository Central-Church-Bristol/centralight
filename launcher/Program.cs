using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("CentraLight")]
[assembly: AssemblyProduct("CentraLight")]
[assembly: AssemblyDescription("CentraLight: the church's lights")]

namespace CentraLight
{
    // Starts CentraLight (main.py with the folder's own Python) without a console window. It has to stay in the
    // CentraLight folder, next to main.py: put a shortcut to it on the desktop or in the Start menu instead of moving it.
    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            string dir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            string pythonw = Path.Combine(dir, ".venv", "Scripts", "pythonw.exe");
            string script = Path.Combine(dir, "main.py");

            if (!File.Exists(pythonw) || !File.Exists(script))
            {
                string setup = Path.Combine(dir, "run.bat");
                if (File.Exists(setup))
                {
                    // the first time: run.bat sets everything up, then starts CentraLight
                    Process.Start(new ProcessStartInfo
                    {
                        FileName = setup,
                        WorkingDirectory = dir,
                        UseShellExecute = true,
                    });
                    return;
                }

                MessageBox.Show(
                    "CentraLight.exe has to stay in the CentraLight folder, next to main.py.\n\n" +
                    "Put a shortcut to it on the desktop instead of moving it.",
                    "CentraLight",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                return;
            }

            var start = new ProcessStartInfo();
            start.FileName = pythonw;
            start.WorkingDirectory = dir;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.Arguments = Quote(script);
            foreach (string arg in args)
                start.Arguments += " " + Quote(arg);

            try
            {
                Process.Start(start);
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "CentraLight couldn't start:\n\n" + ex.Message,
                    "CentraLight",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
            }
        }

        static string Quote(string value)
        {
            if (value.Length > 0 && value.IndexOfAny(new[] { ' ', '\t', '"' }) < 0)
                return value;
            var text = new StringBuilder("\"");
            int backslashes = 0;
            foreach (char c in value)
            {
                if (c == '\\')
                {
                    backslashes++;
                    continue;
                }
                if (c == '"')
                {
                    text.Append('\\', backslashes * 2 + 1);
                    text.Append('"');
                }
                else
                {
                    text.Append('\\', backslashes);
                    text.Append(c);
                }
                backslashes = 0;
            }
            text.Append('\\', backslashes * 2);
            text.Append('"');
            return text.ToString();
        }
    }
}

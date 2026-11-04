using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net.Http;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class WindowsTray
{
    private const string Home = "http://127.0.0.1:5173/";
    private const string Feed = "http://127.0.0.1:8000/api/reminders";

    public sealed class ReminderFeed
    {
        public Reminder[] items { get; set; }
    }

    public sealed class Reminder
    {
        public string id { get; set; }
        public int version { get; set; }
        public string status { get; set; }
        public string phase { get; set; }
        public TaskInfo task { get; set; }
    }

    public sealed class TaskInfo
    {
        public string title { get; set; }
    }

    [STAThread]
    private static void Main()
    {
        bool first;
        using (var instance = new Mutex(true, @"Local\CampusInbox.MainProject.Tray", out first))
        {
            if (!first) return;
            Application.EnableVisualStyles();
            using (var tray = new TrayContext()) Application.Run(tray);
        }
    }

    private sealed class TrayContext : ApplicationContext
    {
        private readonly NotifyIcon icon = new NotifyIcon();
        private readonly ContextMenuStrip menu = new ContextMenuStrip();
        private readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
        private readonly HttpClient client = new HttpClient(
            new HttpClientHandler { AllowAutoRedirect = false, UseProxy = false });
        private readonly JavaScriptSerializer json = new JavaScriptSerializer();
        private readonly string receiptPath;
        private List<string> receipts;
        private bool baseline;
        private bool paused;
        private bool reading;
        private bool closed;
        private int generation;

        public TrayContext()
        {
            string directory = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "CampusInbox", "tray");
            receiptPath = Path.Combine(directory, "reminder-receipts.json");
            try
            {
                if (new FileInfo(receiptPath).Length <= 1024 * 1024)
                    receipts = json.Deserialize<List<string>>(File.ReadAllText(receiptPath));
            }
            catch (IOException) {}
            catch (UnauthorizedAccessException) {}
            catch (ArgumentException) {}
            catch (InvalidOperationException) {}
            baseline = receipts == null;
            receipts = receipts ?? new List<string>();
            json.MaxJsonLength = 8 * 1024 * 1024;
            client.MaxResponseContentBufferSize = json.MaxJsonLength;
            client.Timeout = TimeSpan.FromSeconds(5);

            icon.Icon = SystemIcons.Information;
            icon.Text = "CampusInbox";
            icon.ContextMenuStrip = menu;
            menu.Items.Add("打开 CampusInbox", null, (sender, args) => Open(Home));
            menu.Items.Add("打开截止提醒", null, (sender, args) => Open(Home + "reminders"));
            var pause = menu.Items.Add("暂停桌面提醒");
            pause.Click += (sender, args) =>
            {
                paused = !paused;
                generation++;
                pause.Text = paused ? "恢复桌面提醒" : "暂停桌面提醒";
                icon.Text = paused ? "CampusInbox · 提醒已暂停" : "CampusInbox";
            };
            menu.Items.Add("退出托盘", null, (sender, args) => ExitThread());
            icon.DoubleClick += (sender, args) => Open(Home);
            icon.BalloonTipClicked += (sender, args) => Open(Home + "reminders");
            icon.Visible = true;
            timer.Interval = 15000;
            timer.Tick += Poll;
            timer.Start();
            // Install a Windows Forms synchronization context before the first async poll.
            var initial = new System.Windows.Forms.Timer { Interval = 100 };
            initial.Tick += (sender, args) => { initial.Dispose(); Poll(sender, args); };
            initial.Start();
        }

        private static void Open(string url)
        {
            try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }
            catch (System.ComponentModel.Win32Exception)
            {
                MessageBox.Show("无法打开浏览器，请手动访问 " + url, "CampusInbox");
            }
        }

        private async void Poll(object sender, EventArgs args)
        {
            if (closed || paused || reading) return;
            int current = generation;
            reading = true;
            try
            {
                string body = await client.GetStringAsync(Feed);
                if (closed || paused || current != generation) return;
                ReminderFeed feed = json.Deserialize<ReminderFeed>(body);
                if (feed == null || feed.items == null) throw new InvalidDataException();
                var seen = new HashSet<string>(receipts, StringComparer.Ordinal);
                var next = new List<string>(receipts);
                Reminder alert = null;
                foreach (Reminder item in feed.items)
                {
                    if (item == null || item.status != "unread" || item.task == null ||
                        String.IsNullOrEmpty(item.id)) continue;
                    string key = item.id + ":" + item.version;
                    if (!seen.Add(key)) continue;
                    next.Add(key);
                    if (baseline) continue;
                    alert = item;
                    break;
                }
                if (baseline || alert != null)
                {
                    if (next.Count > 5000) next.RemoveRange(0, next.Count - 5000);
                    Save(next);
                    receipts = next;
                    baseline = false;
                }
                icon.Text = "CampusInbox · 桌面提醒运行中";
                if (alert != null)
                {
                    string title = (alert.task.title ?? "课程任务").Trim();
                    if (title.Length > 120) title = title.Substring(0, 120);
                    icon.ShowBalloonTip(6000, "CampusInbox",
                        (alert.phase == "overdue" ? "已逾期：" : "即将截止：") + title,
                        ToolTipIcon.Info);
                }
            }
            catch (Exception)
            {
                if (!closed && !paused && current == generation)
                    icon.Text = "CampusInbox · 提醒暂不可用，将自动重试";
            }
            finally { reading = false; }
        }

        private void Save(List<string> next)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(receiptPath));
            string temporary = receiptPath + ".tmp";
            File.WriteAllText(temporary, json.Serialize(next));
            if (File.Exists(receiptPath)) File.Replace(temporary, receiptPath, null);
            else File.Move(temporary, receiptPath);
        }

        protected override void Dispose(bool disposing)
        {
            closed = true;
            if (disposing)
            {
                timer.Dispose();
                icon.Visible = false;
                icon.Dispose();
                menu.Dispose();
                client.Dispose();
            }
            base.Dispose(disposing);
        }
    }
}

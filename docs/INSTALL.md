# Installing Roster Builder

[English](#english) · [العربية](#arabic)

<a name="english"></a>
## English

### 1. Get the installer

Choose one way:

- **A. Download it from GitHub (easiest)**
  - **Releases** page: download `RosterBuilder_Setup_<version>.exe` from the newest release.
  - Or open **Actions → Build Windows installer**, click the newest green run, and download the **RosterBuilder-Setup-…** artifact under *Artifacts*. It's a .zip; unzip it to get the setup file.
  - The installer is built automatically on GitHub's Windows machines on every update.
- **B. Build it on your own Windows PC**
  1. Install **Python 3.11 or newer** from python.org (3.14 is tested). Tick **Add python.exe to PATH**.
  2. Download this repository: **Code → Download ZIP**, then unzip it. Or use `git clone`.
  3. Double-click `roster_builder\packaging\build_exe.bat` and keep the window open. The first run takes about 10 minutes and downloads about 400 MB.
     - The script builds the program, then installs **Inno Setup 6** with winget if it's missing.
     - If winget isn't available, it asks you to install Inno Setup from https://jrsoftware.org/isdl.php and run the script again.
  4. When it says **Done**, the installer is `dist\installer\RosterBuilder_Setup_<version>.exe`.
     - That one file is all you carry to other computers, on a USB stick, by e-mail or via a shared folder.
     - The program folder `dist\RosterBuilder\` also runs without installing. Copy the *whole* folder.

### 2. Install on a computer

1. Double-click `RosterBuilder_Setup_<version>.exe`.
   - It installs **for the current user only**, so no administrator rights are needed.
   - The setup window can be English or Arabic.
2. Keep the folder it suggests (`%LOCALAPPDATA%\Programs\RosterBuilder`). Tick **Create a desktop shortcut** if you want one.
3. Click **Install**, then **Finish**. If "Launch Roster Builder" is ticked, the app starts straight away.
4. If Windows shows **"Windows protected your PC"**, click **More info → Run anyway**. The app is not code-signed.

The app is then in the **Start menu** under *Roster Builder*. To update, run a newer setup file over the old one; your data is kept.

### 3. First start

- **Import from my Excel roster…** fills staff, shifts, coverage, leave and fixed shifts from the old roster workbook (the INPUT sheet). Your Excel file is never changed.
- **Start empty** starts from scratch. A checklist guides the setup.
- The language is under **View → Language → English / العربية**.

### 4. Move your data to another computer

1. On the old PC, choose **File → Backup now**.
2. Open the data folder: **Help → About → Open data folder**, then the `backups` folder. Copy the newest backup file.
3. On the new PC, install the app, open it, choose **File → Restore…** and pick that file.

### 5. Uninstall

Use **Settings → Apps → Roster Builder → Uninstall**, or the uninstall entry in the Start menu.

**Your data is not deleted.** The database, backups and log stay in `%APPDATA%\RosterBuilder`, so a later re-install finds everything again. Delete that folder by hand only if you really want to erase the data.

### 6. Troubleshooting

- **Error log:** if the app can't start, it shows a message and writes `RosterBuilder_error.log` to `%APPDATA%\RosterBuilder`. **Help → About** shows the exact location.
- **Diagnostic launch from the source folder:** `roster_builder\packaging\run_debug.bat` starts the app with a console and writes `run_debug.log`.
- **Quick check of a build:** in `dist\RosterBuilder`, run `RosterBuilder.exe --smoke-test`, then `echo %errorlevel%`. 0 means it's fine.

---

<a name="arabic"></a>
<div dir="rtl">

## العربية

### 1. الحصول على ملف التثبيت

- **أ. التنزيل من GitHub (الأسهل)**
  - من صفحة **Releases** نزّل `RosterBuilder_Setup_<الإصدار>.exe`.
  - أو افتح **Actions ← Build Windows installer**، واختر أحدث تشغيل ناجح (أخضر)، ثم نزّل الملف من قسم *Artifacts*. ستحصل على ملف مضغوط، فكّه لتحصل على ملف التثبيت.
- **ب. البناء على حاسوبك (ويندوز)**
  1. ثبّت **Python 3.11 أو أحدث** من python.org، وحدّد **Add python.exe to PATH**.
  2. نزّل المستودع (**Code ← Download ZIP**) وفكّ الضغط.
  3. انقر نقرًا مزدوجًا على `roster_builder\packaging\build_exe.bat` واترك النافذة مفتوحة.
     - تستغرق المرة الأولى نحو 10 دقائق، وتنزّل نحو 400 ميغابايت.
     - إن لم يكن Inno Setup 6 مثبّتًا، يحاول السكربت تثبيته تلقائيًا. إن لم ينجح، نزّله من https://jrsoftware.org/isdl.php ثم شغّل السكربت مرة أخرى.
  4. عند ظهور **Done** تجد ملف التثبيت في `dist\installer\RosterBuilder_Setup_<الإصدار>.exe`. هذا الملف الواحد هو كل ما تنقله إلى الأجهزة الأخرى.

### 2. التثبيت على جهاز

1. انقر نقرًا مزدوجًا على ملف التثبيت. يُثبَّت البرنامج للمستخدم الحالي فقط، ولا يحتاج صلاحيات المسؤول.
2. أبقِ المجلد المقترح، وحدّد إنشاء اختصار على سطح المكتب إن رغبت.
3. اضغط **Install** ثم **Finish**.
4. إذا ظهرت رسالة «Windows protected your PC» فاختر **More info ← Run anyway**.

### 3. التشغيل الأول

- **الاستيراد من ملف Excel** يملأ الموظفين والمناوبات والتغطية والإجازات من الملف القديم دون أن يغيّره.
- **البدء من الصفر** يعرض قائمة بخطوات الإعداد.
- لتغيير اللغة: **عرض ← اللغة ← English / العربية**.

### 4. نقل البيانات إلى جهاز آخر

1. على الجهاز القديم: **ملف ← نسخة احتياطية الآن**.
2. انسخ أحدث ملف من مجلد `backups`. تصل إليه من **مساعدة ← حول ← فتح مجلد البيانات**.
3. على الجهاز الجديد: ثبّت البرنامج، ثم **ملف ← استعادة…** واختر الملف.

### 5. إلغاء التثبيت

من **الإعدادات ← التطبيقات**. **لا تُحذف بياناتك**، فهي تبقى في `%APPDATA%\RosterBuilder`.

### 6. حل المشكلات

- إذا لم يعمل البرنامج، يُكتب سجل الأخطاء `RosterBuilder_error.log` في `%APPDATA%\RosterBuilder`. يظهر مكانه في **مساعدة ← حول**.
- يمكن تشغيل `roster_builder\packaging\run_debug.bat` من مجلد المصدر للتشخيص.

</div>

# Roster Builder – how to build the setup.exe

You need a Windows PC with internet (first build only) and the project folder (the folder that contains `roster_builder\`).

1. Install **Python 3.14** from https://www.python.org (tick **Add python.exe to PATH**). Python 3.11 or newer also works.
2. Open the project folder and double-click **`roster_builder\packaging\build_exe.bat`**. Leave the window open.
   * First run: about 10 minutes and about 400 MB of downloads. Later runs are faster.
   * The script builds the program, then looks for **Inno Setup 6**. If it is missing, it installs it with winget. If winget is not available it tells you to download Inno Setup from https://jrsoftware.org/isdl.php (Inno Setup 6; recent versions also add the Arabic setup language) – install it and run the script again.
3. When it says **Done**, the installer is:

   `dist\installer\RosterBuilder_Setup_<version>.exe`

   Give this one file to the users. The program folder `dist\RosterBuilder\` also works on its own.

**What the setup does:** installs for the current user only (no administrator rights) into `%LOCALAPPDATA%\Programs\RosterBuilder`, adds a Start-menu entry and an optional desktop shortcut, and offers **Launch Roster Builder** at the end. The setup is in English or Arabic (Arabic when the installed Inno Setup includes it).
**Uninstalling does not delete the user's data** (`%APPDATA%\RosterBuilder`: the database, the backups and the log). To move to another PC: **File → Backup now**, copy the backup file to the other PC, then **File → Restore…** there.

Optional: run `roster_builder\packaging\cleanup_project.bat` to delete old developer files from the project folder (it shows the list and asks you to type YES first).

The user guides are in this folder: `USER_GUIDE.md` (English) and `USER_GUIDE_AR.md` (Arabic). They are shown inside the program under **Help → User guide**.

---

## بالعربية – كيفية إنشاء ملف التثبيت setup.exe

تحتاج إلى حاسوب ويندوز متصل بالإنترنت (في أول مرة فقط) ومجلد المشروع (المجلد الذي يحوي `roster_builder\`).

1. ثبّت **Python 3.14** من https://www.python.org (حدّد **Add python.exe to PATH**).
2. انقر مرتين على **`roster_builder\packaging\build_exe.bat`** واترك النافذة مفتوحة. في المرة الأولى يستغرق نحو 10 دقائق وينزّل نحو 400 ميغابايت.
   * بعد بناء البرنامج يبحث السكربت عن **Inno Setup 6**. إن لم يجده ثبّته بواسطة winget. وإن لم يتوفر winget فسيطلب منك تنزيله من https://jrsoftware.org/isdl.php (Inno Setup 6؛ الإصدارات الحديثة تضيف لغة التثبيت العربية) ثم تشغيل السكربت مرة أخرى.
3. عندما تظهر كلمة **Done** تجد ملف التثبيت هنا:

   `dist\installer\RosterBuilder_Setup_<الإصدار>.exe`

   سلّم هذا الملف الواحد للمستخدمين. ومجلد البرنامج `dist\RosterBuilder\` يعمل وحده أيضًا.

**ماذا يفعل ملف التثبيت:** يُثبّت للمستخدم الحالي فقط (دون صلاحيات المسؤول) في `%LOCALAPPDATA%\Programs\RosterBuilder`، ويضيف اختصارًا في قائمة «ابدأ» واختصارًا اختياريًا على سطح المكتب، ويعرض خيار **تشغيل Roster Builder** في النهاية. نافذة التثبيت بالإنجليزية أو العربية.
**إلغاء التثبيت لا يحذف بيانات المستخدم** (`%APPDATA%\RosterBuilder`: قاعدة البيانات والنسخ الاحتياطية والسجل). للانتقال إلى حاسوب آخر: **ملف ← نسخة احتياطية الآن**، انسخ ملف النسخة الاحتياطية إلى الحاسوب الآخر، ثم **ملف ← استعادة…** هناك.

اختياري: شغّل `roster_builder\packaging\cleanup_project.bat` لحذف ملفات المطوّر القديمة من مجلد المشروع (يعرض القائمة ويطلب منك كتابة YES أولًا).

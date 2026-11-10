# Workflow

[English](#english) · [العربية](#arabic)

<a name="english"></a>
## English

```
 ONCE                                   EVERY MONTH
 ┌───────────────┐                      ┌───────────┐   ┌──────────────────────┐   ┌──────────────┐
 │ Import Excel  │                      │ New month │──▶│ Leave · Fixed shifts │──▶│ Check inputs │
 │  or start     │──▶ Settings ─▶ ...   └───────────┘   └──────────────────────┘   └──────┬───────┘
 │  empty        │    Receptions                                                          ▼
 └───────────────┘    Shifts            ┌──────────────┐   ┌──────────────────────┐   ┌───────┐
                      Coverage          │ Export PDF / │◀──│ Review · edit cells  │◀──│ Build │
                      Employees         │ Excel·Print  │   │ · rebuild keeping    │   └───────┘
                                        └──────────────┘   │   my changes         │
                                                           └──────────────────────┘
```

### A. One-time setup

1. **Welcome screen.** Choose **Import from my Excel roster…** to pre-fill everything from the old workbook, or **Start empty**.
2. **Settings.** Set the week start, longest shift, normal hours per week, minimum rest between shifts, the men-only hours, days off per week, weekend days and the overtime rules: whether overtime is allowed, the weekly overtime limit, the longest extension, and "keep one free day per week".
3. **Receptions.** Add the receptions (desks) to be staffed.
4. **Shifts.** Enter start and end times; a split shift has a second part. The app names each shift from its times.
5. **Coverage.** For each reception, enter the hours that need at least *n* people, how many of them must be men, and the maximum. The 24-hour bars show any gaps.
6. **Employees.** For each person enter:
   - gender, home reception and any reception they can also cover
   - fixed days off (two days that are next to each other)
   - allowed and preferred shifts
   - hours per week
   - overtime allowed and **overtime priority (1–10)**

Every change saves automatically. **Ctrl+Z** undoes and **Ctrl+Y** redoes.

### B. Every month

1. **New month.** Copies the current staff, shifts and coverage. Leave that overlaps the month is carried over, and you can copy fixed shifts too. Earlier months stay unchanged.
2. **Leave and fixed shifts.** Enter who is away and any shifts that must be placed exactly. Demand rows are optional.
3. **Check inputs.** Lists mistakes grouped by screen.
   - **Show** jumps to the field.
   - **Fix** applies a safe correction, only when you click it.
4. **Build.** Runs in the background with progress and a Cancel button; the default limit is 60 s.
   - The result shows shifts, overtime hours, overtime per priority rank and the rule checks.
   - If no roster is possible, the app lists the conflicting entries with links to them.
5. **Review.** On the **Roster** screen, days are rows and staff are columns, grouped by reception.
   - Colours: overtime / extension, fixed, helping elsewhere, leave and days off.
   - A coverage strip under the grid shows the hours covered per day.
   - **Problems 0 ✓** means every rule is kept.
6. **Edit if needed.** Double-click a cell to set another shift, OFF, another reception, overtime, or an earlier start / later finish.
   - The rules are re-checked straight away and any broken rule is flagged on the cell.
   - Edited cells are pinned, and **Rebuild keeping my changes** keeps them exactly as you set them.
7. **Export.** Use **Export PDF…**, **Export Excel…** or **Print…**. The Excel file also includes the checks, weekly hours and coverage tables.

### C. Housekeeping

- A backup is made automatically every time the app starts, and the last 10 are kept. **File → Backup now** / **Restore…** do it by hand.
- **Moving to another PC:** back up on the old PC, copy the backup file, then Restore on the new one.
- **A built month is locked.** Use **Re-open month** to change its inputs.

---

<a name="arabic"></a>
<div dir="rtl">

## العربية

### أ. الإعداد لمرة واحدة

1. **شاشة الترحيب:** اختر الاستيراد من ملف Excel القديم أو البدء من الصفر.
2. **الإعدادات:** أول أيام الأسبوع، وأطول مناوبة، وساعات الأسبوع، وأقل راحة بين المناوبات، وساعات الرجال فقط، وأيام الراحة، وعطلة نهاية الأسبوع، وقواعد العمل الإضافي.
3. **مكاتب الاستقبال.**
4. **المناوبات:** أدخل وقت البداية ووقت النهاية، ويُكوَّن اسم المناوبة تلقائيًا.
5. **التغطية:** عدد الأشخاص المطلوب لكل ساعة، وكم منهم يجب أن يكونوا رجالًا، والحد الأقصى.
6. **الموظفون:** لكل شخص:
   - الجنس، ومكتب الاستقبال الأساسي، وأي مكتب آخر يمكنه تغطيته
   - أيام الراحة الثابتة (يومان متتاليان)
   - المناوبات المسموح بها والمفضلة
   - ساعات العمل في الأسبوع
   - السماح بالعمل الإضافي و**أولوية العمل الإضافي (1–10)**

يُحفظ كل تغيير تلقائيًا، ويمكنك التراجع بـ Ctrl+Z.

### ب. كل شهر

1. **شهر جديد:** ينسخ الموظفين والمناوبات والتغطية الحالية، وتنتقل إليه الإجازات المتداخلة معه.
2. **الإجازات والمناوبات الثابتة.**
3. **فحص المدخلات:** يعرض الأخطاء مع زر «عرض» للانتقال إلى الحقل، وزر «إصلاح» لا يُطبَّق إلا عند الضغط عليه.
4. **الإنشاء:** يعمل في الخلفية مع إمكانية الإلغاء، والحد الافتراضي 60 ثانية.
5. **المراجعة:** شاشة **الجدول** ملوّنة، وتحتها شريط يبيّن التغطية لكل يوم.
6. **التعديل:** انقر نقرًا مزدوجًا على أي خلية لتغييرها.
   - تُفحص القواعد فورًا، وتُثبَّت الخلايا التي عدّلتها.
   - «إعادة الإنشاء مع إبقاء تعديلاتي» يحافظ عليها كما هي.
7. **التصدير:** PDF أو Excel أو الطباعة.

### ج. الصيانة

- تُنشأ نسخة احتياطية تلقائيًا عند كل تشغيل.
- للنقل إلى جهاز آخر: «نسخة احتياطية الآن» على الجهاز القديم، ثم «استعادة» على الجهاز الجديد.

</div>

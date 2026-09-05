# Campaign carousel — 6 posts, 1080×1080

Ready to post to Facebook and Instagram as a single carousel, in this order.
Every screen in them is real output from the app — cropped and composed, never
redrawn.

| # | File | Says |
|---|---|---|
| 1 | `1-cover.png` | سنترك كله في شاشة واحدة — the dashboard |
| 2 | `2-scan.png` | الطالب يمسح كارته ويدخل — the accepted scan, 16 ms |
| 3 | `3-subjects.png` | الطالب يشترك في أكتر من مادة — 4 subjects, 2,000 ج.م |
| 4 | `4-money.png` | تعرف مين دفع ومين لسه — charges, badges, month totals |
| 5 | `5-reports.png` | المتأخرات بأرقام الأهالي — arrears with guardian phones |
| 6 | `6-cta.png` | ابدأ في سنترك من النهارده — features, phone and email |

## Contact on slide 6

The closing slide carries **+20 102 083 3615** and **codaco2026@gmail.com**.
Both are wrapped in `dir="ltr"` isolation — Latin digits inside an Arabic line
reorder without it, and a scrambled phone number on the call-to-action slide is
the one typo that costs a real customer. Check it once on your own screen before
the campaign goes out.

To change either, edit the `.contact` block in `../tools/carousel.html` and
re-render.

## Design notes

- **Type** is Cairo, the same font the app itself is set in, loaded from
  `static/fonts/`. **Colour** is the app's own teal ramp from
  `static/css/themes.css`. The ads and the product look like one thing.
- **The dark ground is deliberate.** Every screenshot is a light UI, so a dark
  card-on-dark-ground layout makes the product the brightest thing in the frame.
- **Crops are tight on purpose.** A full 3120px screenshot shrunk into a 1080
  square is unreadable on a phone; each slide shows only the rows and columns
  its headline is talking about. Slide 4 bleeds off both edges to buy the table
  another 15% of scale.
- Slides 3 and 4 pair a real screenshot with **stat tiles set in our own type** —
  the numbers are the demo center's actual figures, but rendered large enough to
  read at thumbnail size.

## Suggested captions

**1 —** سنتر كامل بيتدار من شاشة واحدة: حضور، اشتراكات، وفلوس. اسحب لتشوف 👈

**2 —** الطالب يمسح كارته، والشاشة تقوله «تم تسجيل الحضور» باسمه ومجموعته — في 16 جزء من الألف من الثانية. مفيش كشف أسماء، ومفيش وقت ضايع في أول الحصة.

**3 —** الطالب اللي بياخد 4 مواد عندك = 4 اشتراكات، كل واحد بمجموعته ورسومه. النظام بيحسب إجمالي الشهر لوحده، ومحدش بيتسجّل مرتين.

**4 —** في أي لحظة تعرف: المستحق كام، المحصّل كام، والمتبقي كام — ومين بالظبط اللي لسه ما دفعش. دفعات جزئية لكل مادة، وإيصال مطبوع لكل عملية.

**5 —** تقرير المتأخرات بيطلعلك أسماء الطلاب وأرقام أولياء أمورهم في مكان واحد — وتصدّره PDF أو Excel بضغطة، وتبدأ تتصل.

**6 —** جاهز يشتغل في سنترك من النهارده. كلّمنا على 01020833615 أو ابعتلنا على codaco2026@gmail.com واحجز عرض تجريبي.

## Re-rendering

Edit `../tools/carousel.html` — headlines, stat tiles and crop rectangles are
all plain HTML — then:

```sh
node campaign/tools/render_carousel.mjs campaign/tools/carousel.html campaign/social
```

Playwright must be installed in the directory you run node from. Each `.shot`
carries `data-crop="x,y,w,h"` in source-screenshot pixels and `data-w` for the
displayed width; the script does the scaling.

The raw screenshots these are built from are in `../screenshots/ar/`, with a
full index in that folder's README.

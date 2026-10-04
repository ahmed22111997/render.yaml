# نشر الموقع مجانًا (Render.com)

الموقع عبارة عن FastAPI backend + صفحة HTML واحدة. محتاج سيرفر بايثون حقيقي شغال
باستمرار (مش استضافة ثابتة زي GitHub Pages)، عشان edge-tts بيعمل اتصال إنترنت
لحظة التوليد. **Render.com** بيدي Free Web Service بيوفي بالغرض.

## 1) جهّز مستودع GitHub
1. اعمل repo جديد على GitHub (خاص أو عام، مش فارق).
2. ارفع فيه الملفات دي بالظبط:
   - `app.py`
   - `podcast_tts.py`
   - `requirements.txt`
   - `static/index.html`
   - `render.yaml` (اختياري، بيسهّل الإعداد التلقائي)

   ```bash
   cd podcast-site
   git init
   git add .
   git commit -m "Podcast TTS website"
   git branch -M main
   git remote add origin https://github.com/USERNAME/REPO.git
   git push -u origin main
   ```

## 2) اعمل حساب على Render
- روح على https://render.com وسجّل بحساب GitHub (مجاني).

## 3) أنشئ Web Service
1. من الداشبورد: **New +** → **Web Service**.
2. اختار الـ repo اللي رفعته.
3. الإعدادات:
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app:app --host 0.0.0.0 --port $PORT`
   - **Instance Type**: `Free`
4. دوس **Create Web Service**. أول نشر بياخد كام دقيقة.

> ملحوظة: خطة Render المجانية بتنام (sleep) لو الموقع مستخدمش لمدة، وبتصحى تاني
> أول ما حد يفتحه (بتاخد ٣٠-٥٠ ثانية أول طلب). ده طبيعي في الخطة المجانية.

## 4) افتح الموقع
بعد ما ينتهي الـ deploy هيديك رابط شكله:
`https://your-app-name.onrender.com`

افتحه وجرّب تولّد حلقة.

## بدائل مجانية تانية (لو حابب تقارن)
- **Railway.app** — فيه رصيد مجاني شهري محدود، إعداد شبيه بـ Render.
- **Fly.io** — فيه free allowance، محتاج CLI (`flyctl`) بدل واجهة الويب.
- **Google Cloud Run** — free tier سخي، لكن الإعداد أعقد شوية (محتاج Dockerfile).

كل البدائل دي محتاجة سيرفر بايثون حقيقي زي Render بالظبط — الموقع مش هيشتغل على
استضافة ملفات ثابتة (Netlify/Vercel static, GitHub Pages) لأن فيه معالجة صوت
وطلبات شبكة من السيرفر نفسه.

## تحويل الحلقة لفيديو (صور + صوت + سابتايتل محروق)
الميزة دي بتستخدم ffmpeg نفسه اللي بيشتغل بيه توليد الصوت (نفس حزمة
`imageio-ffmpeg` في requirements.txt)، وفيها دعم `libass` لحرق الترجمة جوه
الفيديو، فمحتاجة حاجة إضافية — بس تأكد إن `requirements.txt` فيه
`python-multipart` (مطلوب لرفع الصور) قبل ما تعمل deploy.

## ملاحظات تشغيلية
- الملفات الناتجة (MP3/WAV/SRT) بتتخزن مؤقتًا في مجلد `jobs/<job_id>/` على
  السيرفر، وبتتمسح تلقائيًا بعد ساعتين (عشان القرص المجاني محدود المساحة).
- الخطة المجانية عادة بتديك قرص مؤقت (ephemeral) — يعني الملفات ممكن تتمسح لو
  السيرفر أعاد التشغيل، فخلي المستخدم يحمّل الملف فورًا بعد ما يخلص.
- تقدر تغيّر مدة الاحتفاظ بالملفات من `_cleanup_old_jobs()` في `app.py`.

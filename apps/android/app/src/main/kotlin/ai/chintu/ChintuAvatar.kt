package ai.chintu

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.os.SystemClock
import android.provider.Settings
import android.view.View
import java.util.Random
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.min
import kotlin.math.sin

/**
 * Procedural, local-only Chintu orb wrapped in a reactive voice-flow halo.
 *
 * - Listening: the halo bars and ripples follow microphone RMS; motes stream *into* the orb.
 * - Speaking: a synthetic speech envelope drives the halo and inner pulse; motes stream *out*.
 * - Thinking: a scanner sweep plus orbiting motes. Idle: slow breathing and faint rings.
 *
 * The speaking envelope is a visual animation, not an output audio meter. If a real output meter is
 * wired later it only needs to write [level] while the phase is SPEAKING. Honors the system
 * "remove animations" setting by dropping decorative motion.
 */
class ChintuAvatar(context: Context) : View(context) {
    var phase = VoicePhase.IDLE
        set(value) {
            if (field != value) {
                ripple(0.6f)
                if (value != VoicePhase.LISTENING) level = 0f
            }
            field = value
            contentDescription = "Chintu ${value.name.lowercase()}. Tap for voice control."
            invalidate()
        }

    /** Live 0..1 level. Listening uses microphone RMS; a future output meter may set it while speaking. */
    var level = 0f
        set(value) { field = value.coerceIn(0f, 1f) }
    /** Note mode: a pen traces an ink line around the orb and text lines fill inside, until the note is closed. */
    var writing = false
        set(value) { if (field != value) { field = value; inkBurstAt = if (value) SystemClock.uptimeMillis() else 0L; invalidate() } }
    private var inkBurstAt = 0L
    /** Call when a chunk of the note reached the computer: the nib flares and the ring ripples once. */
    fun inkBurst() { inkBurstAt = SystemClock.uptimeMillis(); ripple(.8f); invalidate() }
    private val inkColor = Color.rgb(246, 238, 255)
    private val inkPath = Path()

    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeCap = Paint.Cap.ROUND }
    private val path = Path()
    private val outline = Path()
    private val arc = RectF()
    private val random = Random()

    private val body = RadialGradient(.35f, -.4f, 1.65f,
        intArrayOf(Color.rgb(68, 12, 180), Color.rgb(91, 11, 205), Color.rgb(194, 43, 222), Color.rgb(241, 135, 248)),
        floatArrayOf(0f, .42f, .77f, 1f), Shader.TileMode.CLAMP)
    private val glow = RadialGradient(0f, .1f, 1.9f,
        intArrayOf(Color.argb(70, 125, 25, 223), Color.argb(16, 177, 69, 244), Color.TRANSPARENT),
        floatArrayOf(0f, .62f, 1f), Shader.TileMode.CLAMP)
    private val silk = LinearGradient(-1f, -.8f, .8f, .8f,
        intArrayOf(Color.WHITE, Color.rgb(252, 230, 255), Color.rgb(242, 104, 250), Color.argb(15, 194, 74, 255)),
        floatArrayOf(0f, .24f, .59f, 1f), Shader.TileMode.CLAMP)
    private val ribbon = LinearGradient(-.7f, -.6f, .6f, .8f,
        intArrayOf(Color.argb(0, 255, 255, 255), Color.argb(230, 250, 212, 255), Color.argb(140, 218, 104, 255), Color.argb(0, 230, 130, 255)),
        floatArrayOf(0f, .43f, .67f, 1f), Shader.TileMode.CLAMP)
    private val rim = LinearGradient(-1f, -.7f, .8f, 1f,
        intArrayOf(Color.argb(190, 254, 236, 255), Color.argb(30, 227, 102, 255), Color.argb(105, 245, 168, 255)),
        null, Shader.TileMode.CLAMP)
    private val pulse = RadialGradient(0f, 0f, 1f,
        intArrayOf(Color.argb(210, 255, 242, 255), Color.argb(90, 236, 180, 255), Color.TRANSPARENT),
        floatArrayOf(0f, .45f, 1f), Shader.TileMode.CLAMP)

    private val haloColor = Color.rgb(226, 190, 255)
    private val ringColor = Color.rgb(199, 169, 255)
    private val rippleColor = Color.rgb(240, 200, 255)
    private val inwardColor = Color.rgb(250, 232, 255)
    private val outwardColor = Color.rgb(246, 160, 250)

    private var lastFrame = 0L
    private var clock = 0f
    private var smooth = 0f
    private var energy = .12f
    private var sampleClock = 0f
    private var head = 0
    private val history = FloatArray(HISTORY)
    private var lastRipple = 0L
    private var risen = false
    private val rippleStart = LongArray(MAX_RIPPLES)
    private val rippleStrength = FloatArray(MAX_RIPPLES)
    private val pAngle = FloatArray(MAX_PARTICLES)
    private val pRadius = FloatArray(MAX_PARTICLES)
    private val pSpeed = FloatArray(MAX_PARTICLES)
    private val pLife = FloatArray(MAX_PARTICLES)
    private val pDuration = FloatArray(MAX_PARTICLES)
    private val pSize = FloatArray(MAX_PARTICLES)
    private val pDir = IntArray(MAX_PARTICLES)
    private var spawnDebt = 0f
    private var reduceMotion = false

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        reduceMotion = try {
            Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f
        } catch (_: Exception) { false }
        lastFrame = 0L
        invalidate()
    }

    override fun onWindowVisibilityChanged(visibility: Int) {
        super.onWindowVisibilityChanged(visibility)
        if (visibility == VISIBLE) { lastFrame = 0L; invalidate() }
    }

    private fun ripple(strength: Float) {
        if (reduceMotion) return
        val now = SystemClock.uptimeMillis()
        val slot = (0 until MAX_RIPPLES).firstOrNull { rippleStart[it] == 0L } ?: return
        rippleStart[slot] = now; rippleStrength[slot] = strength.coerceIn(0f, 1f)
    }

    /** Syllable/word/phrase rhythm so the orb reads as talking without measuring the speaker output. */
    private fun syntheticSpeech(t: Float): Float {
        val syllable = .5f + .5f * sin(t * 25f)
        val word = .55f + .45f * sin(t * 4.1f + 1.3f)
        val phrase = if (sin(t * .9f) > -.55f) 1f else .15f
        val flutter = .5f + .5f * sin(t * 23f) * sin(t * 7.7f)
        return ((.25f + .75f * syllable * word) * phrase * (.7f + .3f * flutter)).coerceIn(0f, 1f)
    }

    private fun update(dt: Float, now: Long) {
        val target = when (phase) {
            VoicePhase.LISTENING -> level
            VoicePhase.SPEAKING -> if (level > .02f) level else syntheticSpeech(clock)
            VoicePhase.THINKING -> .25f + .1f * sin(clock * 3f)
            VoicePhase.IDLE -> .08f + .05f * sin(clock * 1.1f)
        }
        val tau = if (target > smooth) .045f else .16f
        smooth += (target - smooth) * (1f - exp(-dt / tau))
        val energyTarget = when (phase) {
            VoicePhase.IDLE -> .12f
            VoicePhase.LISTENING -> .45f + smooth * .5f
            VoicePhase.THINKING -> .55f
            VoicePhase.SPEAKING -> .7f + smooth * .3f
        }
        energy += (energyTarget - energy) * (1f - exp(-dt / .25f))
        sampleClock += dt
        while (sampleClock >= SAMPLE_SECONDS) {
            sampleClock -= SAMPLE_SECONDS
            head = (head + 1) % HISTORY
            history[head] = smooth
        }
        if (reduceMotion) return
        when (phase) {
            VoicePhase.LISTENING -> {
                val above = smooth > .45f
                if (above && !risen && now - lastRipple > 220) { ripple(smooth); lastRipple = now }
                risen = above
            }
            VoicePhase.SPEAKING -> if (smooth > .55f && now - lastRipple > 340) { ripple(smooth); lastRipple = now }
            else -> risen = false
        }
        val rate = when (phase) {
            VoicePhase.LISTENING -> 14f + smooth * 70f
            VoicePhase.SPEAKING -> 20f + smooth * 60f
            VoicePhase.THINKING -> 12f
            VoicePhase.IDLE -> 3f
        }
        spawnDebt += rate * dt
        while (spawnDebt >= 1f) { spawnDebt -= 1f; spawn() }
        for (i in 0 until MAX_PARTICLES) {
            if (pLife[i] <= 0f) continue
            pLife[i] -= dt / pDuration[i]
            when (pDir[i]) {
                -1 -> { pRadius[i] -= pSpeed[i] * dt; if (pRadius[i] < 1f) pLife[i] = 0f }
                1 -> pRadius[i] += pSpeed[i] * dt
                else -> { pAngle[i] += pSpeed[i] * dt; pRadius[i] += sin(clock * 3f + pAngle[i]) * .12f * dt }
            }
        }
    }

    private fun spawn() {
        val i = (0 until MAX_PARTICLES).firstOrNull { pLife[it] <= 0f } ?: return
        val r = random.nextFloat()
        pAngle[i] = random.nextFloat() * (2f * PI).toFloat()
        pLife[i] = 1f
        pSize[i] = .016f + random.nextFloat() * .026f
        when (phase) {
            VoicePhase.LISTENING -> {
                pDir[i] = -1; pRadius[i] = 1.75f + r * .3f
                pSpeed[i] = .7f + random.nextFloat() * .6f + smooth * .7f
                pDuration[i] = (pRadius[i] - 1f) / pSpeed[i] + .1f
            }
            VoicePhase.SPEAKING -> {
                pDir[i] = 1; pRadius[i] = 1.02f + r * .08f
                pSpeed[i] = .45f + random.nextFloat() * .55f + smooth * .4f
                pDuration[i] = .85f / pSpeed[i]
            }
            VoicePhase.THINKING -> {
                pDir[i] = 0; pRadius[i] = 1.18f + r * .32f
                pSpeed[i] = (1.1f + random.nextFloat()) * (if (random.nextBoolean()) 1f else -1f)
                pDuration[i] = 1.4f + random.nextFloat() * 1.6f
            }
            VoicePhase.IDLE -> {
                pDir[i] = 1; pRadius[i] = 1.05f + r * .2f
                pSpeed[i] = .08f + random.nextFloat() * .08f
                pDuration[i] = 3f + random.nextFloat() * 2f
            }
        }
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val now = SystemClock.uptimeMillis()
        val dt = if (lastFrame == 0L) .016f else (now - lastFrame).coerceIn(0L, 100L) / 1000f
        lastFrame = now
        clock += dt
        update(dt, now)
        val unit = min(width, height) * .29f
        if (unit <= 0f) return
        canvas.save()
        canvas.translate(width / 2f, height / 2f)
        canvas.scale(unit, unit)
        drawGlow(canvas)
        drawRipples(canvas, now)
        drawRings(canvas)
        if (writing && phase != VoicePhase.SPEAKING) { drawOrb(canvas); drawWriting(canvas, now) }
        else { drawParticles(canvas); drawHalo(canvas); drawOrb(canvas) }
        canvas.restore()
        if (isShown && windowVisibility == VISIBLE) {
            val quiet = phase == VoicePhase.IDLE && !writing && rippleStart.all { it == 0L }
            if (reduceMotion) postInvalidateDelayed(80) else if (quiet) postInvalidateDelayed(40) else postInvalidateOnAnimation()
        }
    }

    private fun drawGlow(canvas: Canvas) {
        fill.style = Paint.Style.FILL; fill.shader = glow; fill.alpha = (150 + energy * 105).toInt()
        canvas.drawCircle(0f, .1f, 1.9f, fill)
        fill.alpha = 255; fill.shader = null
    }

    private fun drawRipples(canvas: Canvas, now: Long) {
        stroke.color = rippleColor
        for (i in 0 until MAX_RIPPLES) {
            val start = rippleStart[i]
            if (start == 0L) continue
            val p = (now - start) / 1100f
            if (p >= 1f) { rippleStart[i] = 0L; continue }
            val ease = 1f - (1f - p) * (1f - p)
            stroke.alpha = ((1f - p) * (70f + 130f * rippleStrength[i])).toInt()
            stroke.strokeWidth = .03f * (1f - p * .7f)
            canvas.drawCircle(0f, 0f, 1.04f + ease * .78f, stroke)
        }
    }

    private fun drawRings(canvas: Canvas) {
        val spin = if (reduceMotion) 0f else clock * (.5f + energy * 1.6f)
        stroke.color = ringColor
        for (ring in RINGS) {
            val r = ring[0]; val segments = ring[1].toInt(); val span = ring[2]; val speed = ring[3]
            arc.set(-r, -r, r, r)
            stroke.strokeWidth = ring[4]; stroke.alpha = (40f + energy * 150f).toInt()
            val base = spin * speed
            for (k in 0 until segments) canvas.drawArc(arc, base + k * 360f / segments, span, false, stroke)
        }
        // Static ticks on the outer edge give the halo a dial to read against.
        stroke.strokeWidth = .01f; stroke.alpha = (25f + energy * 70f).toInt()
        for (k in 0 until 36) {
            val a = (k * 2.0 * PI / 36).toFloat(); val c = cos(a); val s = sin(a)
            val len = if (k % 9 == 0) .07f else .035f
            canvas.drawLine(c * 1.6f, s * 1.6f, c * (1.6f + len), s * (1.6f + len), stroke)
        }
        if (phase == VoicePhase.THINKING && !reduceMotion) {
            arc.set(-1.3f, -1.3f, 1.3f, 1.3f)
            val sweep = (clock * 300f) % 360f
            for (k in 0 until 12) {
                stroke.alpha = (190f * (1f - k / 12f)).toInt(); stroke.strokeWidth = .03f - k * .0018f
                canvas.drawArc(arc, sweep - k * 7f, 6f, false, stroke)
            }
        }
    }

    private fun drawParticles(canvas: Canvas) {
        if (reduceMotion) return
        fill.style = Paint.Style.FILL; fill.shader = null
        for (i in 0 until MAX_PARTICLES) {
            val life = pLife[i]
            if (life <= 0f) continue
            val progress = 1f - life
            val fade = sin(PI.toFloat() * progress).coerceIn(0f, 1f)
            val dim = if (pDir[i] == 1 && phase == VoicePhase.IDLE) .35f else 1f
            val color = if (pDir[i] == -1) inwardColor else outwardColor
            val c = cos(pAngle[i]); val s = sin(pAngle[i]); val r = pRadius[i]
            if (pDir[i] != 0) {
                val tail = r - pDir[i] * pSpeed[i] * .07f
                stroke.color = color; stroke.alpha = (fade * dim * 110f).toInt(); stroke.strokeWidth = pSize[i] * 1.1f
                canvas.drawLine(c * r, s * r, c * tail, s * tail, stroke)
            }
            fill.color = color; fill.alpha = (fade * dim * 235f).toInt()
            canvas.drawCircle(c * r, s * r, pSize[i], fill)
        }
        fill.alpha = 255
    }

    private fun drawHalo(canvas: Canvas) {
        val amp = when (phase) {
            VoicePhase.LISTENING -> .34f
            VoicePhase.SPEAKING -> .3f
            VoicePhase.THINKING -> .12f
            VoicePhase.IDLE -> .07f
        }
        val inner = 1.09f
        stroke.color = haloColor
        for (i in 0 until BARS) {
            val d = min(i, BARS - i)
            val offset = d * (HISTORY - 1) / (BARS / 2)
            val h = history[((head - offset) % HISTORY + HISTORY) % HISTORY]
            val shimmer = if (reduceMotion) .5f else .5f + .5f * sin(clock * 5f + i * 1.7f)
            val v = (h * (.75f + .25f * shimmer)).coerceIn(0f, 1f)
            val len = .025f + v * amp * (.6f + .4f * energy)
            val a = (i * 2.0 * PI / BARS - PI / 2).toFloat(); val c = cos(a); val s = sin(a)
            // Soft bloom only where there is signal; the resting ring stays a thin dotted dial.
            if (v > .12f) {
                stroke.alpha = (v * 70f).toInt(); stroke.strokeWidth = .06f
                canvas.drawLine(c * inner, s * inner, c * (inner + len), s * (inner + len), stroke)
            }
            stroke.alpha = (55f + energy * 60f + v * 140f).toInt().coerceAtMost(255); stroke.strokeWidth = .016f
            canvas.drawLine(c * inner, s * inner, c * (inner + len), s * (inner + len), stroke)
        }
    }

    private fun drawWriting(canvas: Canvas, now: Long) {
        val t = clock
        val burst = ((now - inkBurstAt) / 600f).let { if (inkBurstAt == 0L || it > 1f) 0f else 1f - it }
        val heard = if (phase == VoicePhase.LISTENING) smooth else .15f
        // Cursive ink line circling the orb: the head advances with time, the tail fades behind it.
        fun radiusAt(a: Float) = 1.2f + .045f * sin(7f * a + t * 2.2f) + .02f * sin(13f * a - t * 3.1f) + heard * .03f * sin(23f * a + t * 9f)
        val head = (if (reduceMotion) 0f else t * 1.1f) % (2f * PI).toFloat()
        val segments = 84; val span = (300.0 * PI / 180.0).toFloat()
        for (i in 0 until segments) {
            val a0 = head - span * (i + 1) / segments; val a1 = head - span * i / segments
            val r0 = radiusAt(a0); val r1 = radiusAt(a1)
            val fade = 1f - i / segments.toFloat()
            stroke.color = inkColor; stroke.alpha = (200f * fade * fade + 20f).toInt().coerceAtMost(255)
            stroke.strokeWidth = .018f + .022f * fade
            canvas.drawLine(cos(a0) * r0, sin(a0) * r0, cos(a1) * r1, sin(a1) * r1, stroke)
        }
        // Pen nib at the head: a short tilted stroke and a glowing point that flares when a chunk lands.
        val hr = radiusAt(head); val hx = cos(head) * hr; val hy = sin(head) * hr
        val tilt = head + .9f
        stroke.color = ringColor; stroke.alpha = 230; stroke.strokeWidth = .05f
        canvas.drawLine(hx, hy, hx + cos(tilt) * .22f, hy + sin(tilt) * .22f, stroke)
        stroke.color = inkColor; stroke.alpha = 255; stroke.strokeWidth = .03f
        canvas.drawLine(hx, hy, hx + cos(tilt) * .2f, hy + sin(tilt) * .2f, stroke)
        fill.style = Paint.Style.FILL; fill.shader = null
        fill.color = inkColor; fill.alpha = (60 + 120 * burst).toInt()
        canvas.drawCircle(hx, hy, .09f + .1f * burst + heard * .04f, fill)
        fill.alpha = 255; canvas.drawCircle(hx, hy, .035f, fill)
        // Lines of text filling inside the orb, like a page being written and turned.
        canvas.save(); canvas.clipPath(outline)
        for (i in 0 until 4) {
            val y = -.45f + i * .3f
            val progress = ((t * .32f + i * .27f) % 1f)
            val width = 1.15f * (if (progress < .85f) progress / .85f else 1f - (progress - .85f) / .15f)
            val wobble = .012f * sin(t * 6f + i)
            stroke.color = inkColor; stroke.alpha = (if (progress < .85f) 150 else (150 * (1f - (progress - .85f) / .15f)).toInt())
            stroke.strokeWidth = .04f
            canvas.drawLine(-.58f, y + wobble, -.58f + width, y - wobble, stroke)
        }
        canvas.restore()
        stroke.alpha = 255
    }

    private fun drawOrb(canvas: Canvas) {
        val motion = when (phase) {
            VoicePhase.IDLE -> .015f
            VoicePhase.LISTENING -> .02f + smooth * .06f
            VoicePhase.THINKING -> .025f
            VoicePhase.SPEAKING -> .03f + smooth * .05f
        }
        val speed = when (phase) { VoicePhase.SPEAKING -> 4.4f; VoicePhase.THINKING -> 2.3f; VoicePhase.LISTENING -> 2.8f; else -> .7f }
        val breathe = 1f + motion * sin(clock * speed)
        val t = clock
        canvas.save()
        canvas.scale(breathe, breathe)
        outline.reset()
        outline.moveTo(-.02f, -1f)
        outline.cubicTo(.61f, -1.05f, .99f, -.58f, 1f, -.04f)
        outline.cubicTo(1.08f, .56f, .53f, 1.03f, -.02f, 1f)
        outline.cubicTo(-.69f, 1.03f, -1.10f, .47f, -.97f, -.12f)
        outline.cubicTo(-.90f, -.67f, -.48f, -.99f, -.02f, -1f)
        outline.close()
        canvas.save(); canvas.clipPath(outline)
        fill.style = Paint.Style.FILL; fill.shader = body; canvas.drawPaint(fill)
        // A broad pearlescent fold, slowly turning inside the violet core.
        canvas.rotate(6f * sin(t * .38f))
        path.reset(); path.moveTo(-.42f, -1.2f)
        path.cubicTo(-1.3f, -.8f, -1.2f, .35f, -.59f, .73f)
        path.cubicTo(.25f, 1.31f, .99f, .28f, .78f, -.03f)
        path.cubicTo(.60f, .72f, -.50f, .56f, -.46f, -.22f)
        path.cubicTo(-.48f, -.65f, -.13f, -.75f, -.42f, -1.2f); path.close()
        fill.shader = silk; canvas.drawPath(path, fill)
        // Layered translucent folds supply depth without a bitmap or network asset.
        for (i in 0..2) {
            canvas.save(); canvas.rotate(i * 28f + sin(t * .55f + i) * 9f)
            val drift = sin(t * .8f + i) * .07f
            path.reset(); path.moveTo(-1.1f, .05f + drift)
            path.cubicTo(-.65f, -.71f, -.13f, .82f, .52f, -.17f + drift)
            path.cubicTo(.91f, -.72f, 1.35f, .22f, 1.12f, .44f)
            path.cubicTo(.73f, -.34f, .57f, .36f, .13f, .52f)
            path.cubicTo(-.47f, .78f, -.67f, -.14f, -1.1f, .05f + drift); path.close()
            fill.shader = ribbon; fill.alpha = 170 - i * 30
            canvas.drawPath(path, fill); fill.alpha = 255; canvas.restore()
        }
        // Voice leaving the orb lights its core from the inside.
        if (phase == VoicePhase.SPEAKING) {
            canvas.rotate(-6f * sin(t * .38f))
            fill.shader = pulse; fill.alpha = (smooth * 160f).toInt()
            canvas.drawCircle(0f, 0f, 1f, fill); fill.alpha = 255
        }
        canvas.restore()
        fill.shader = rim; fill.style = Paint.Style.STROKE; fill.strokeWidth = .012f
        canvas.drawPath(outline, fill)
        fill.shader = null; fill.style = Paint.Style.FILL
        canvas.restore()
    }

    private companion object {
        const val HISTORY = 48
        const val SAMPLE_SECONDS = .025f
        const val BARS = 64
        const val MAX_RIPPLES = 6
        const val MAX_PARTICLES = 72
        /** radius, segment count, segment span (deg), spin multiplier, stroke width. */
        val RINGS = arrayOf(
            floatArrayOf(1.24f, 3f, 70f, 18f, .014f),
            floatArrayOf(1.38f, 5f, 28f, -11f, .01f),
            floatArrayOf(1.52f, 2f, 120f, 7f, .008f),
        )
    }
}

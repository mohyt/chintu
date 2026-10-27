package ai.chintu

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.text.InputType
import android.text.Editable
import android.text.TextWatcher
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.*
import ai.chintu.core.EventCursor
import ai.chintu.core.SubmissionGuard
import ai.chintu.core.VoiceCommand
import ai.chintu.core.DestinationMatcher
import ai.chintu.core.ChatTranscript
import org.json.JSONObject
import java.util.concurrent.Executors

class MainActivity : Activity() {
    private val ink = Color.rgb(244, 240, 255)
    private val muted = Color.rgb(150, 145, 174)
    private val accent = Color.rgb(199, 169, 255)
    private val base = Color.rgb(6, 3, 22)
    private val surface = Color.rgb(24, 17, 44)
    private val executor = Executors.newSingleThreadExecutor()
    private val handler = Handler(Looper.getMainLooper())
    private val preferences by lazy { getSharedPreferences("chintu-ui", MODE_PRIVATE) }
    private lateinit var computers: ComputerStore
    private lateinit var store: ClientStore
    private lateinit var guard: SubmissionGuard
    private lateinit var cursor: EventCursor
    private lateinit var speech: SpeechController
    private lateinit var audio: AudioRouteController
    private lateinit var avatar: ChintuAvatar
    private lateinit var transcript: EditText
    private lateinit var language: EditText
    private lateinit var status: TextView
    private lateinit var phaseLabel: TextView
    private lateinit var greetingLabel: TextView
    private lateinit var noteButton: Button
    /** Note mode streams each finished phrase to the daemon as a chunk until the owner taps ✎ again. */
    private var noteMode = false
    /** Owner paused dictation with the mic button; the watchdog must not restart listening until resumed. */
    private var notePaused = false
    private var noteId: String? = null
    private var noteWords = 0
    private var noteChunks = 0
    private var notePartial = ""
    private var noteSentWords = 0
    private val noteFlushInterval = 6000L
    /** Fixed-cadence flush: whatever new words the recogniser has heard since the last flush go to the daemon now. */
    private val noteFlush = object : Runnable {
        override fun run() {
            if (!noteMode) return
            flushNote(notePartial, final = false)
            // Self-healing: an open note always has the microphone on while the app is in front.
            if (!notePaused && foreground && !inDialog && !busy && !waitingForRoute && !speech.isListening && !speech.isSpeaking &&
                System.currentTimeMillis() - lastListenAttempt > 3000) requestListen()
            handler.postDelayed(this, noteFlushInterval)
        }
    }
    private lateinit var activityButton: Button
    private lateinit var orbBadge: TextView
    private lateinit var activityList: LinearLayout
    private lateinit var activityStatus: TextView
    private lateinit var quiet: Switch
    private var olderEntries = mutableListOf<ActivityStore.Entry>()
    private var olderCursor: String? = null
    private var olderExhausted = false
    private fun activityStore() = ActivityStore(this, store.gateway)
    /** Voice output is allowed unless the owner muted replies or switched on quiet mode. */
    private fun speakingAllowed() = ::quiet.isInitialized && autoSpeech.isChecked && !quiet.isChecked
    private var city: String? = null
    private var greeted = false
    private var suggestionIndex = 0
    /** Things people can say straight away; shown on the home screen instead of a generic prompt. */
    private val suggestions = listOf(
        "What's on my calendar tomorrow?",
        "Summarize my unread emails",
        "What did I miss in Slack today?",
        "What are my to-dos for today?",
        "Draft a reply to the last email from my manager",
        "Remind me to call home at six",
        "Any meetings I can skip this week?",
        "What's the weather for my commute?",
        "Find the deck I shared last week",
        "Give me a two-line brief before my next meeting",
    ).shuffled()
    private val rotateSuggestion = object : Runnable {
        override fun run() {
            if (!foreground) return
            if (avatar.phase == VoicePhase.IDLE && !busy && !noteMode) {
                suggestionIndex = (suggestionIndex + 1) % suggestions.size
                phaseLabel.animate().alpha(0f).setDuration(160).withEndAction {
                    if (avatar.phase == VoicePhase.IDLE) phaseLabel.text = currentSuggestion()
                    phaseLabel.animate().alpha(1f).setDuration(220).start()
                }.start()
            }
            handler.postDelayed(this, 7000)
        }
    }
    private lateinit var output: TextView
    private lateinit var destinationLabel: TextView
    private lateinit var connectionLabel: TextView
    private lateinit var audioLabel: TextView
    private lateinit var runLabel: TextView
    private lateinit var mic: ImageButton
    private lateinit var audioButton: ImageButton
    private var audioPicker: android.app.Dialog? = null
    private var openAudioAfterPermission = false
    private lateinit var send: Button
    private lateinit var handsFree: Switch
    private lateinit var wakeWord: Switch
    private var wakeLaunch = false  // opened by the wake word: listen once the activity is visible
    private lateinit var autoSend: Switch
    private lateinit var online: Switch
    private lateinit var autoSpeech: Switch
    private lateinit var audioList: LinearLayout
    private lateinit var computerList: LinearLayout
    private lateinit var discoveryList: LinearLayout
    private lateinit var discoveryStatus: TextView
    private lateinit var project: Spinner
    private lateinit var provider: Spinner
    private lateinit var profile: Spinner
    private lateinit var task: Spinner
    private val pages = mutableListOf<ViewGroup>()
    /** The chat sheet is a conversation surface: voice keeps working while it is open, unlike forms. */
    private var chatOpen = false
    private lateinit var chatLabel: TextView
    private lateinit var chatTitle: TextView
    private lateinit var chatSubtitle: TextView
    private lateinit var chatMessages: LinearLayout
    private lateinit var chatScroll: ScrollView
    /** Turns of the current conversation, oldest first, and each turn's streamed output (tool steps and reply text). */
    private var chatTurns = mutableListOf<JSONObject>()
    private val chatEvents = mutableMapOf<String, ChatTranscript>()
    /** Finished turns whose full output was fetched on request. */
    private val chatExpanded = mutableSetOf<String>()
    private var overlay: android.app.Dialog? = null
    /** Push channel for events (server-sent events); polling stays on as the safety net and takes over when it is down. */
    private var stream: EventStream? = null
    private var streamLive = false
    private var lastPollAt = 0L
    private val renderSoon = Runnable { if (chatOpen) renderChat() }
    private var sheet: android.app.Dialog? = null
    private var projects = emptyList<JSONObject>()
    private var capabilities = emptyList<JSONObject>()
    private var tasks = emptyList<JSONObject>()
    private var selectedComputer: Computer? = null
    private var discovered = emptyList<JSONObject>()
    @Volatile private var foreground = false
    private var polling = false
    private var busy = false
    private var connected = false
    private var epoch = 0
    private var lastReply = ""
    private var reviewPending = false
    private var inDialog = false
    private var rebuildingSelections = false
    private var settingTranscript = false
    private var lastAudioId: Int? = null
    private var waitingForRoute = false
    private var pendingReply: Pair<String, String>? = null
    private val routeTimeout = Runnable {
        if (waitingForRoute) {
            // Don't strand the user: Android brings up the headset link when capture starts, so listen anyway.
            waitingForRoute = false
            val tag = language.text.toString().trim()
            if (foreground && !inDialog && !busy && tag.isNotBlank() && !speech.isListening && !speech.isSpeaking) {
                showStatus("Audio device is slow to connect. Listening anyway."); speech.listen(online.isChecked, tag, dictation = noteMode && !notePaused)
            } else {
                handsFree.isChecked = false
                showStatus("The audio device hasn't finished connecting. Choose it again using the audio button.")
            }
        }
    }
    private val poll = Runnable { if (foreground && polling) pollEvents() }
    private val listenAgain = Runnable {
        if (foreground && handsFree.isChecked && !inDialog && !busy && !reviewPending &&
            !speech.isListening && !speech.isSpeaking) requestListen()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.statusBarColor = base; window.navigationBarColor = base
        computers = ComputerStore(this)
        selectedComputer = computers.all().firstOrNull { it.url == computers.active }
        // A single saved computer is the obvious destination; select it instead of asking.
        if (selectedComputer == null) computers.all().singleOrNull()?.let { computers.activate(it.url); selectedComputer = it }
        store = ClientStore(this, selectedComputer?.url.orEmpty()); guard = SubmissionGuard(store); cursor = EventCursor(store.cursor)
        buildUi(savedInstanceState)
        speech = SpeechController(this, ::showStatus, ::onUtterance, ::setPhase,
            { avatar.level = it }, { scheduleListening() }, { retryable ->
                if (noteMode) { if (retryable && !notePaused) handler.postDelayed({ if (noteMode && !notePaused && foreground && !inDialog) requestListen() }, retryDelay()) else if (!retryable) showStatus("Speech stopped. Tap the mic to continue, or ✎ to finish."); return@SpeechController }
                if (!retryable) handsFree.isChecked = false
                else if (handsFree.isChecked) scheduleListening(1600)
                else if (autoRetries < 1) { autoRetries++; handler.postDelayed({ if (foreground && !inDialog) requestListen() }, retryDelay()) }
            })
        audio = AudioRouteController(this, ::audioChanged, ::showStatus)
        renderComputers(); updateDestination(); audioChanged()
        restoreOpenNote()
        handleWakeIntent(intent)
        if (guard.pending() != null) showStatus("An instruction is waiting for a receipt. Retry it on this computer.")
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
    private fun shape(color: Int, radius: Int = 22, border: Int? = null) = GradientDrawable().apply {
        setColor(color); cornerRadius = dp(radius).toFloat(); border?.let { setStroke(dp(1), it) }
    }
    private fun column(parent: ViewGroup? = null, padding: Int = 0): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL; setPadding(dp(padding), dp(padding), dp(padding), dp(padding)); parent?.addView(this)
    }
    private fun gap(parent: ViewGroup, height: Int = 12) { parent.addView(View(this), ViewGroup.LayoutParams(1, dp(height))) }
    private fun label(parent: ViewGroup, text: String, size: Float = 15f, color: Int = ink, bold: Boolean = false): TextView = TextView(this).apply {
        this.text = text; textSize = size; setTextColor(color); if (bold) typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        setLineSpacing(dp(3).toFloat(), 1f); parent.addView(this)
    }
    private fun card(parent: LinearLayout): LinearLayout = column(padding = 18).apply {
        background = shape(surface); parent.addView(this, LinearLayout.LayoutParams(-1, -2)); gap(parent)
    }
    private fun button(parent: ViewGroup, text: String, primary: Boolean = false, action: () -> Unit): Button = Button(this).apply {
        this.text = text; isAllCaps = false; textSize = 15f; minHeight = dp(48)
        setTextColor(if (primary) base else ink); background = shape(if (primary) accent else Color.rgb(38, 28, 61), 16)
        setPadding(dp(14), dp(10), dp(14), dp(10)); setOnClickListener { action() }
        parent.addView(this, ViewGroup.LayoutParams(-1, -2))
    }
    private fun pill(text: String, description: String, action: () -> Unit): Button = Button(this).apply {
        this.text = text; isAllCaps = false; textSize = 13f; setTextColor(ink); background = shape(Color.rgb(38, 28, 61), 20)
        setPadding(dp(12), 0, dp(12), 0); minWidth = 0; minimumWidth = 0; minHeight = 0; minimumHeight = 0
        contentDescription = description; setOnClickListener { action() }
    }
    private fun field(parent: ViewGroup, hint: String, value: String = "", single: Boolean = false): EditText = EditText(this).apply {
        this.hint = hint; setText(value); setTextColor(ink); setHintTextColor(muted); textSize = 16f
        setPadding(dp(12), dp(12), dp(12), dp(12)); background = shape(base, 12); setSingleLine(single)
        parent.addView(this, ViewGroup.LayoutParams(-1, -2))
    }
    private fun toggle(parent: ViewGroup, text: String, checked: Boolean = false): Switch = Switch(this).apply {
        this.text = text; textSize = 14f; setTextColor(ink); minHeight = dp(52); isChecked = checked
        setPadding(0, dp(8), 0, dp(8))
        val states = arrayOf(intArrayOf(android.R.attr.state_checked), intArrayOf())
        thumbTintList = android.content.res.ColorStateList(states, intArrayOf(accent, Color.rgb(120, 110, 150)))
        trackTintList = android.content.res.ColorStateList(states, intArrayOf(Color.rgb(96, 70, 150), Color.rgb(44, 36, 70)))
        parent.addView(this, ViewGroup.LayoutParams(-1, -2))
    }
    private fun spinner(parent: ViewGroup): Spinner = Spinner(this).apply {
        minimumHeight = dp(48); background = shape(base, 12, Color.rgb(60, 46, 94))
        setPopupBackgroundDrawable(shape(surface, 16, Color.rgb(72, 48, 109)))
        setPadding(dp(12), dp(4), dp(12), dp(4))
        parent.addView(this, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(8) })
    }
    /** App-styled bottom sheet: dark rounded panel, bold title, × close. Caller shows it. */
    private fun bottomSheet(title: String, body: View, tall: Boolean = false, onDismiss: () -> Unit = {}): android.app.Dialog {
        val panel = column().apply { background = shape(base, 28) }
        val header = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL; setPadding(dp(20), dp(10), dp(12), dp(4)) }
        panel.addView(header)
        header.addView(TextView(this).apply { text = title; textSize = 20f; setTextColor(ink); typeface = Typeface.DEFAULT_BOLD },
            LinearLayout.LayoutParams(0, -2, 1f))
        val dialog = android.app.Dialog(this)
        header.addView(Button(this).apply {
            text = "×"; textSize = 28f; contentDescription = "Close $title"; setTextColor(muted); background = shape(base)
            setPadding(0, 0, 0, 0); setOnClickListener { dialog.dismiss() }
        }, LinearLayout.LayoutParams(dp(48), dp(48)))
        panel.addView(body, LinearLayout.LayoutParams(-1, if (tall) 0 else -2, if (tall) 1f else 0f))
        dialog.setContentView(panel)
        dialog.setOnDismissListener { onDismiss() }
        dialog.window?.apply {
            setBackgroundDrawable(android.graphics.drawable.ColorDrawable(Color.TRANSPARENT))
            setGravity(Gravity.BOTTOM)
            setSoftInputMode(android.view.WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
            addFlags(android.view.WindowManager.LayoutParams.FLAG_DIM_BEHIND)
            attributes = attributes.apply { dimAmount = .6f }
        }
        return dialog
    }
    private fun showSheet(dialog: android.app.Dialog, tall: Boolean = false) {
        dialog.show()
        dialog.window?.setLayout(-1, if (tall) (resources.displayMetrics.heightPixels * .82f).toInt() else -2)
    }
    /** Replaces the stock message dialog for long status text. */
    private fun showMessage(title: String, text: CharSequence) {
        val body = column(padding = 20)
        label(body, text.toString(), 16f, ink); gap(body)
        val dialog = bottomSheet(title, body)
        button(body, "OK", true) { dialog.dismiss() }
        showSheet(dialog)
    }
    private fun buildUi(state: Bundle?) {
        val root = column(padding = 24).apply { setBackgroundColor(base) }
        root.setOnApplyWindowInsetsListener { view, insets ->
            val bars = insets.getInsets(android.view.WindowInsets.Type.systemBars() or android.view.WindowInsets.Type.ime())
            view.setPadding(dp(24) + bars.left, dp(12) + bars.top, dp(24) + bars.right, dp(12) + bars.bottom)
            insets
        }
        val header = FrameLayout(this)
        root.addView(header, LinearLayout.LayoutParams(-1, dp(52)))
        greetingLabel = TextView(this).apply {
            text = greetingText(); textSize = 15f; setTextColor(muted); gravity = Gravity.CENTER
        }
        header.addView(greetingLabel, FrameLayout.LayoutParams(-1, -1).apply { marginStart = dp(48); marginEnd = dp(48) })
        activityButton = Button(this).apply {
            text = "☰"; textSize = 22f; setTextColor(muted); background = shape(base, 24)
            setPadding(0, 0, 0, 0); minWidth = 0; minimumWidth = 0
            contentDescription = "Activity"; setOnClickListener { showActivity() }
        }
        header.addView(activityButton, FrameLayout.LayoutParams(dp(48), dp(48), Gravity.START or Gravity.CENTER_VERTICAL))
        val settingsButton = Button(this).apply {
            text = "⚙"; textSize = 24f; setTextColor(muted); background = shape(base, 24)
            setPadding(0, 0, 0, 0); minWidth = 0; minimumWidth = 0
            contentDescription = "Settings"; setOnClickListener { showPage(2) }
        }
        header.addView(settingsButton, FrameLayout.LayoutParams(dp(48), dp(48), Gravity.END or Gravity.CENTER_VERTICAL))
        phaseLabel = label(root, currentSuggestion(), 23f, ink, true).apply { gravity = Gravity.CENTER; maxLines = 2; minHeight = dp(76) }
        val stage = FrameLayout(this).apply { minimumHeight = dp(160) }
        root.addView(stage, LinearLayout.LayoutParams(-1, 0, 1f))
        avatar = ChintuAvatar(this).apply { isFocusable = true; setOnClickListener { mic.performClick() } }
        stage.addView(avatar, FrameLayout.LayoutParams(-1, -1))
        // Result counter riding on the orb: appears when the daemon finished something the owner hasn't read.
        orbBadge = TextView(this).apply {
            textSize = 13f; setTextColor(base); typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
            background = shape(accent, 16); setPadding(dp(12), dp(6), dp(12), dp(6)); visibility = View.GONE
            contentDescription = "New results. Open activity"; setOnClickListener { showActivity() }
        }
        stage.addView(orbBadge, FrameLayout.LayoutParams(-2, -2, Gravity.TOP or Gravity.END).apply { topMargin = dp(8); marginEnd = dp(8) })
        label(root, "CHINTU", 20f, ink).apply {
            gravity = Gravity.CENTER; letterSpacing = .35f
            contentDescription = "Chintu: Conversational Hands-free Interface to Networked Terminals and Utilities"
            setOnClickListener { showMessage("CHINTU", "Conversational Hands-free Interface to Networked Terminals & Utilities.\n\nYour voice. Your computers.") }
        }
        gap(root, 18)
        status = label(root, "Tap the orb and say it. Hold the mic to type.", 14f, muted).apply {
            gravity = Gravity.CENTER; minHeight = dp(50); maxLines = 2
            ellipsize = android.text.TextUtils.TruncateAt.END
            accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
            setOnClickListener { if (text.isNotBlank()) showMessage("Status", text) }
        }
        destinationLabel = label(root, "Choose a computer  ›", 13f, muted).apply {
            gravity = Gravity.CENTER; minHeight = dp(48); maxLines = 1; ellipsize = android.text.TextUtils.TruncateAt.END
            setOnClickListener { showPage(1) }; isFocusable = true; contentDescription = "Choose execution computer"
        }
        // Voice and chat share one conversation: this row names it and starts a fresh one (a new agent session).
        val chatRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        root.addView(chatRow, LinearLayout.LayoutParams(-1, dp(44)))
        chatLabel = TextView(this).apply {
            text = "💬  New chat  ›"; textSize = 13f; setTextColor(muted); gravity = Gravity.CENTER; maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END; isFocusable = true; minHeight = dp(44)
            contentDescription = "Open the current chat"; setOnClickListener { showConversation() }
        }
        chatRow.addView(chatLabel, LinearLayout.LayoutParams(0, -2, 1f))
        chatRow.addView(pill("＋ New chat", "Start a new chat with a fresh session") { showNewChat() }, LinearLayout.LayoutParams(-2, dp(40)))
        val controls = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        root.addView(controls, LinearLayout.LayoutParams(-1, dp(76)))
        val keyboard = Button(this).apply {
            text = "⌨"; textSize = 25f; contentDescription = "Type or view conversation"
            setTextColor(muted); background = shape(base, 28); setPadding(0, 0, 0, 0)
            setOnClickListener { showConversation() }
        }
        controls.addView(keyboard, LinearLayout.LayoutParams(dp(52), dp(52)))
        noteButton = Button(this).apply {
            text = "✎"; textSize = 24f; contentDescription = "Take a note"
            setTextColor(muted); background = shape(base, 28); setPadding(0, 0, 0, 0); minWidth = 0; minimumWidth = 0
            setOnClickListener { toggleNoteMode() }
        }
        controls.addView(noteButton, LinearLayout.LayoutParams(dp(52), dp(52)).apply { marginStart = dp(4) })
        mic = ImageButton(this).apply {
            setImageResource(android.R.drawable.ic_btn_speak_now)
            imageTintList = android.content.res.ColorStateList.valueOf(ink)
            scaleType = android.widget.ImageView.ScaleType.FIT_CENTER
            background = shape(surface, 36, Color.rgb(72, 48, 109)); setPadding(dp(20), dp(20), dp(20), dp(20))
            contentDescription = "Tap to speak"
            setOnClickListener {
                when {
                    noteMode && !notePaused -> { notePaused = true; flushNote(notePartial, final = true); speech.cancelRecognition(); muteRecognizerTones(false); setPhase(VoicePhase.IDLE); showStatus("Note paused. Tap the mic to continue, or ✎ to finish.") }
                    noteMode -> { notePaused = false; muteRecognizerTones(true); setPhase(VoicePhase.IDLE); showStatus("Listening again."); requestListen() }
                    speech.isSpeaking -> { speech.stopSpeaking(); scheduleListening() }
                    speech.isListening -> speech.finish()
                    else -> { reviewPending = false; autoRetries = 0; requestListen() }
                }
            }
        }
        // Side buttons sit in equal-weight slots so the round mic stays centred under the orb.
        val slot = { view: View -> FrameLayout(this).apply { addView(view, FrameLayout.LayoutParams(dp(52), dp(52), Gravity.CENTER)) } }
        controls.removeView(keyboard); controls.removeView(noteButton)
        // Typing lives behind a long-press on the mic; the home row stays note · speak · audio.
        mic.setOnLongClickListener { showConversation(); true }
        controls.addView(slot(noteButton), LinearLayout.LayoutParams(0, dp(72), 1f))
        controls.addView(mic, LinearLayout.LayoutParams(dp(72), dp(72)))
        audioButton = ImageButton(this).apply {
            setImageResource(android.R.drawable.ic_lock_silent_mode_off)
            imageTintList = android.content.res.ColorStateList.valueOf(muted)
            background = shape(base, 26); setPadding(dp(13), dp(13), dp(13), dp(13))
            contentDescription = "Switch audio device"
            setOnClickListener { showAudioPicker() }
        }
        controls.addView(slot(audioButton), LinearLayout.LayoutParams(0, dp(72), 1f))
        gap(root, 8)
        // Configuration and conversation remain detached until a sheet is requested.
        val chat = column().apply { setPadding(dp(16), 0, dp(16), dp(12)) }
        pages.add(chat)
        repeat(3) { pages.add(ScrollView(this).apply { isFillViewport = false; clipToPadding = false }) }
        val chatHeader = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        chat.addView(chatHeader, LinearLayout.LayoutParams(-1, -2))
        val titles = column()
        chatTitle = label(titles, "New chat", 17f, ink, true).apply { maxLines = 1; ellipsize = android.text.TextUtils.TruncateAt.END }
        chatSubtitle = label(titles, "", 12f, muted).apply { maxLines = 2; ellipsize = android.text.TextUtils.TruncateAt.END }
        chatHeader.addView(titles, LinearLayout.LayoutParams(0, -2, 1f))
        chatHeader.addView(pill("Tools", "Tools the agent can use") { showTools() }, LinearLayout.LayoutParams(-2, dp(40)))
        chatHeader.addView(pill("Chats", "All chats on this computer") { showChats() }, LinearLayout.LayoutParams(-2, dp(40)).apply { marginStart = dp(6) })
        chatHeader.addView(pill("＋", "Start a new chat") { showNewChat() }, LinearLayout.LayoutParams(-2, dp(40)).apply { marginStart = dp(6) })
        output = label(chat, "", 13f, muted).apply { maxLines = 3; ellipsize = android.text.TextUtils.TruncateAt.END; setPadding(0, dp(6), 0, dp(6)) }
        chatScroll = ScrollView(this).apply { isFillViewport = true; clipToPadding = false }
        chatMessages = column(chatScroll)
        chat.addView(chatScroll, LinearLayout.LayoutParams(-1, 0, 1f))
        val composer = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.BOTTOM; setPadding(0, dp(8), 0, 0) }
        chat.addView(composer, LinearLayout.LayoutParams(-1, -2))
        transcript = field(composer, "Message", state?.getString("transcript") ?: store.draft).apply {
            minLines = 1; maxLines = 5; inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
            background = shape(surface, 22); layoutParams = LinearLayout.LayoutParams(0, -2, 1f)
        }
        transcript.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: Editable?) {
                if (settingTranscript || !::speech.isInitialized) return
                waitingForRoute = false; handler.removeCallbacks(routeTimeout)
                speech.cancelRecognition(); handler.removeCallbacks(listenAgain)
                reviewPending = !s.isNullOrBlank(); store.draft = s?.toString().orEmpty()
            }
        })
        composer.addView(ImageButton(this).apply {
            setImageResource(android.R.drawable.ic_btn_speak_now); imageTintList = android.content.res.ColorStateList.valueOf(ink)
            scaleType = android.widget.ImageView.ScaleType.FIT_CENTER; background = shape(surface, 24); setPadding(dp(10), dp(10), dp(10), dp(10))
            contentDescription = "Speak into this chat"
            setOnClickListener { if (speech.isListening) speech.finish() else { reviewPending = false; autoRetries = 0; requestListen() } }
        }, LinearLayout.LayoutParams(dp(48), dp(48)).apply { marginStart = dp(6) })
        send = Button(this).apply {
            text = "➤"; textSize = 20f; setTextColor(base); background = shape(accent, 24); setPadding(0, 0, 0, 0)
            minWidth = 0; minimumWidth = 0; contentDescription = "Send"; setOnClickListener { handleTypedSend() }
        }
        composer.addView(send, LinearLayout.LayoutParams(dp(48), dp(48)).apply { marginStart = dp(6) })
        val devices = column(pages[1], 20)
        val hosts = card(devices)
        label(hosts, "Your computers", 20f, ink, true)
        connectionLabel = label(hosts, "Pair a computer to get started.", 13f, muted)
        computerList = column(hosts); gap(hosts)
        button(hosts, "Add a computer", true) { showComputerDialog(null) }; gap(hosts, 8)
        gap(hosts, 6)
        label(hosts, "Also on your Tailscale network", 15f, ink, true)
        discoveryStatus = label(hosts, "Pair one computer and the others appear here automatically.", 13f, muted)
        discoveryList = column(hosts); gap(hosts, 4)
        button(hosts, "Refresh network") { discover() }
        val audioCard = card(devices)
        label(audioCard, "Audio", 20f, ink, true)
        audioLabel = label(audioCard, "Phone microphone and speaker", 13f, muted)
        audioList = column(audioCard); gap(audioCard, 8)
        button(audioCard, "Switch audio device") { showAudioPicker() }; gap(audioCard, 8)
        button(audioCard, "Bluetooth settings") { startActivity(Intent(Settings.ACTION_BLUETOOTH_SETTINGS)) }
        val settings = column(pages[2], 20)
        button(settings, "Computers & audio  ›") { showPage(1) }; gap(settings, 16)
        val voice = card(settings)
        label(voice, "Conversation", 20f, ink, true)
        handsFree = toggle(voice, "Hands-free while the app is open")
        handsFree.setOnCheckedChangeListener { _, enabled ->
            handler.removeCallbacks(listenAgain)
            if (enabled) { reviewPending = false; scheduleListening() }
            else if (::speech.isInitialized) {
                waitingForRoute = false; handler.removeCallbacks(routeTimeout); speech.cancelRecognition()
            }
        }
        wakeWord = toggle(voice, "Wake word · say \u201cChintu\u201d any time, even with the screen off",
            preferences.getBoolean(WakeWordService.PREF_ENABLED, false))
        wakeWord.setOnCheckedChangeListener { _, enabled ->
            if (enabled) enableWakeWord()
            else { preferences.edit().putBoolean(WakeWordService.PREF_ENABLED, false).apply(); WakeWordService.stop(this) }
        }
        autoSpeech = toggle(voice, "Read replies aloud", preferences.getBoolean("auto_speech", true))
        autoSend = toggle(voice, "Send voice instructions automatically", preferences.getBoolean("auto_send", true))
        quiet = toggle(voice, "Quiet mode · queue silently, read results in Activity", preferences.getBoolean("quiet", false))
        label(voice, "Off means you review your words in the conversation sheet before each send. Hands-free stops when you leave the app.", 12f, muted)
        gap(voice)
        language = field(voice, "Speech language · en-IN", preferences.getString("language", java.util.Locale.getDefault().toLanguageTag()).orEmpty(), true)
        online = toggle(voice, "Allow online Android speech when on-device speech is unavailable", preferences.getBoolean("online", false))
        val execution = card(settings)
        label(execution, "Workspace & agent", 20f, ink, true)
        runLabel = label(execution, "Choose these once for each computer.", 13f, muted)
        label(execution, "A chat keeps the workspace, agent and permission profile it started with. Changing them here starts a new chat with your next message.", 12f, muted)
        project = spinner(execution); provider = spinner(execution); profile = spinner(execution)
        setOptions(project, listOf("Choose workspace")); setOptions(provider, listOf("Choose agent")); setOptions(profile, listOf("Choose permission profile"))
        val selection = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                if (!rebuildingSelections) { updateProfiles(); persistChoices() }
            }
            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }
        project.onItemSelectedListener = selection; provider.onItemSelectedListener = selection
        profile.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) { if (!rebuildingSelections) persistChoices() }
            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }
        gap(execution)
        button(execution, "Reconnect") { selectedComputer?.let { connect(it, false) } ?: showPage(1) }
        val activityPage = column(pages[3], 20)
        activityStatus = label(activityPage, "Final results from your computers. Kept for seven days on this phone.", 13f, muted); gap(activityPage, 8)
        activityList = column(activityPage); gap(activityPage, 8)
        button(activityPage, "Load older from the computer") { loadOlderActivity() }
        val history = card(settings)
        label(history, "Recent tasks", 20f, ink, true)
        task = spinner(history); setOptions(task, listOf("Choose a task"))
        button(history, "Read selected task") { readTask() }; gap(history, 8)
        button(history, "Test text connection") { echo() }
        gap(settings, 8)
        label(settings, "CHINTU · Conversational Hands-free Interface to Networked Terminals & Utilities", 12f, muted).apply { gravity = Gravity.CENTER; letterSpacing = .02f }
        label(settings, "Your voice. Your computers.", 12f, muted).apply { gravity = Gravity.CENTER }
        gap(settings, 16)
        setContentView(root)
    }
    private fun dismissSheet() { sheet?.dismiss() }
    private fun showConversation() {
        if (chatOpen && sheet?.isShowing == true) { renderChat(); return }
        openSheet(0, selectedComputer?.let { "Chat · ${it.name}" } ?: "Chat")
        renderChat(); loadChat()
    }
    private var noteEnteredAt = 0L
    private fun toggleNoteMode() {
        // Debounce: a second tap within 1.5 s of starting is almost always an accidental double tap.
        if (noteMode) { if (System.currentTimeMillis() - noteEnteredAt < 1500) return; finishNote(); return }
        if (selectedComputer == null || !store.isPaired() || !connected) { respond("Connect a computer first, then I can take notes there."); return }
        if (busy) { showStatus("One moment, finishing the current request."); return }
        val origin = store
        speech.pause(); handler.removeCallbacks(listenAgain)
        background("Starting a note…") { ticket ->
            val created = api(origin).call("POST", "/api/v1/notes", "{}")
            ui(ticket) {
                enterNoteMode(created.getString("note_id"), origin.gateway)
                showStatus("Go ahead, I'm writing this down.")
                handler.postDelayed({ if (noteMode && foreground) requestListen() }, 300)
            }
        }
    }
    private fun flushNote(text: String, final: Boolean) {
        val words = text.trim().split(Regex("\\s+")).filter { it.isNotBlank() }
        android.util.Log.i("Chintu", "note flush final=$final heard=${words.size} sent=$noteSentWords listening=${speech.isListening}")
        // Interval flushes keep the last word back: the recogniser is usually still finishing it.
        val upto = if (final) words.size else words.size - 1
        if (upto > noteSentWords) {
            appendNoteChunk(words.subList(noteSentWords, upto).joinToString(" "))
            noteSentWords = upto
        }
        if (final) { notePartial = ""; noteSentWords = 0 }
    }
    private fun enterNoteMode(id: String, gateway: String) {
        noteMode = true; noteId = id; noteWords = 0; noteChunks = 0; notePartial = ""; noteSentWords = 0
        noteEnteredAt = System.currentTimeMillis(); avatar.writing = true; notePaused = false
        muteRecognizerTones(true)
        android.util.Log.i("Chintu", "note mode on: ${id.take(8)}")
        speech.onPartial = { notePartial = it }
        speech.onSessionStart = { notePartial = ""; noteSentWords = 0 }
        handler.removeCallbacks(noteFlush); handler.postDelayed(noteFlush, noteFlushInterval)
        preferences.edit().putString("open_note", id).putString("open_note_gateway", gateway).apply()
        noteButton.setTextColor(accent); noteButton.background = shape(surface, 28, accent)
        setPhase(VoicePhase.IDLE)
    }
    private fun exitNoteMode() {
        noteMode = false; notePaused = false; noteId = null; notePartial = ""; noteSentWords = 0
        avatar.writing = false; muteRecognizerTones(false)
        speech.onPartial = null; speech.onSessionStart = null; handler.removeCallbacks(noteFlush)
        preferences.edit().remove("open_note").remove("open_note_gateway").apply()
        noteButton.setTextColor(muted); noteButton.background = shape(base, 28)
        setPhase(VoicePhase.IDLE)
    }
    /** A note left open by a restart stays open: dictation must never become an instruction. */
    private fun restoreOpenNote() {
        val id = preferences.getString("open_note", null) ?: return
        val gateway = preferences.getString("open_note_gateway", "").orEmpty()
        if (gateway != store.gateway || !store.isPaired()) return
        enterNoteMode(id, gateway)
        showStatus("Your note is still open. Keep talking, or tap ✎ to finish it.")
        handler.postDelayed({ if (noteMode && foreground && !inDialog && !busy && !speech.isListening) requestListen() }, 1500)
    }
    private fun appendNoteChunk(text: String) {
        val id = noteId ?: return; val origin = store; val ticket = epoch
        if (text.isBlank()) return
        executor.execute {
            try {
                val progress = api(origin).call("POST", "/api/v1/notes/$id/chunks", JSONObject().put("text", text).toString())
                ui(ticket) {
                    noteWords = progress.getInt("words"); noteChunks = progress.getInt("chunks")
                    showStatus("Noted · $noteWords words so far. “${text.take(70)}”")
                    avatar.inkBurst()
                }
            } catch (failure: Exception) {
                ui(ticket) { showStatus("Couldn't send that part of the note; I'll keep trying with the next.") }
            }
        }
    }
    private fun finishNote() {
        val id = noteId ?: run { exitNoteMode(); return }; val origin = store
        // Flush whatever the recogniser has heard so far, including the held-back last word, before cutting it off.
        flushNote(notePartial, final = true)
        speech.cancelRecognition()
        background("Saving your note…") { ticket ->
            val saved = api(origin).call("POST", "/api/v1/notes/$id/finish", "{}")
            ui(ticket) {
                exitNoteMode()
                val words = saved.getInt("words")
                lastReply = saved.optString("text"); output.text = "Note saved as ${saved.getString("file")}\n\n${saved.optString("text")}"
                respond(if (words == 0) "That note was empty, so I didn't keep it." else "Saved your note, $words words, on ${selectedComputer?.name ?: "the computer"}.")
            }
        }
    }
    private fun updateActivityBadge() {
        if (!::activityButton.isInitialized) return
        val unseen = if (store.gateway.isBlank()) 0 else activityStore().unseen()
        activityButton.setTextColor(if (unseen > 0) accent else muted)
        activityButton.contentDescription = if (unseen > 0) "Activity, $unseen new results" else "Activity"
        if (::orbBadge.isInitialized) {
            orbBadge.text = if (unseen == 1) "1 new result" else "$unseen new results"
            if (unseen > 0 && orbBadge.visibility != View.VISIBLE) {
                orbBadge.alpha = 0f; orbBadge.visibility = View.VISIBLE; orbBadge.animate().alpha(1f).setDuration(250).start()
            } else if (unseen == 0) orbBadge.visibility = View.GONE
        }
    }
    private fun showActivity() {
        olderEntries.clear(); olderCursor = null; olderExhausted = false
        renderActivity(); openSheet(3, "Activity")
        if (store.gateway.isNotBlank()) { activityStore().markAllSeen(); updateActivityBadge() }
    }
    private fun timeAgo(stamp: Long): String {
        if (stamp <= 0L) return ""
        val minutes = (System.currentTimeMillis() - stamp) / 60_000
        return when { minutes < 1 -> "just now"; minutes < 60 -> "$minutes min ago"; minutes < 1440 -> "${minutes / 60} h ago"; else -> "${minutes / 1440} d ago" }
    }
    private fun renderActivity() {
        if (!::activityList.isInitialized) return
        activityList.removeAllViews()
        val recent = if (store.gateway.isBlank()) emptyList() else activityStore().all()
        val rows = recent + olderEntries.filter { older -> recent.none { it.taskId == older.taskId } }
        activityStatus.text = when {
            store.gateway.isBlank() -> "Connect a computer to see its activity."
            rows.isEmpty() -> "Nothing yet. Results from ${selectedComputer?.name ?: "your computer"} will appear here."
            else -> "Final results only. The last seven days stay on this phone; older ones load from ${selectedComputer?.name ?: "the computer"}."
        }
        rows.forEach { entry ->
            val row = column(padding = 14).apply { background = shape(surface, 18); isClickable = true; isFocusable = true }
            val head = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
            row.addView(head, LinearLayout.LayoutParams(-1, -2))
            val color = when (entry.state) { "succeeded" -> Color.rgb(140, 230, 170); "failed", "interrupted" -> Color.rgb(255, 150, 140); "cancelled" -> muted; else -> accent }
            head.addView(TextView(this).apply { text = if (entry.terminal) "●" else "◌"; textSize = 13f; setTextColor(color) }, LinearLayout.LayoutParams(dp(20), -2))
            head.addView(TextView(this).apply { text = entry.instruction.ifBlank { "(no text)" }; textSize = 15f; setTextColor(ink); maxLines = 2
                ellipsize = android.text.TextUtils.TruncateAt.END; typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL) }, LinearLayout.LayoutParams(0, -2, 1f))
            head.addView(TextView(this).apply { text = timeAgo(entry.updatedAt); textSize = 12f; setTextColor(muted) }, LinearLayout.LayoutParams(-2, -2))
            val detail = when {
                entry.terminal && entry.summary.isNotBlank() -> entry.summary
                entry.terminal -> "Finished: ${entry.state}"
                entry.state == "queued" -> "Queued, waiting its turn"
                else -> "Working on it…"
            }
            label(row, detail, 13f, if (entry.terminal) ink else muted).apply { maxLines = 4; ellipsize = android.text.TextUtils.TruncateAt.END; setPadding(dp(20), dp(4), 0, 0) }
            row.setOnClickListener { showMessage(entry.instruction.take(60), detail) }
            activityList.addView(row, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(8) })
        }
    }
    /** Pages through the daemon's task history, oldest-first beyond what the phone keeps. */
    private fun loadOlderActivity() {
        if (store.gateway.isBlank() || !store.isPaired()) { showStatus("Connect a computer first."); return }
        if (olderExhausted) { showStatus("That's everything the computer has."); return }
        val origin = store; val cursorValue = olderCursor
        background("Loading older activity…") { ticket ->
            val suffix = cursorValue?.let { "&cursor=" + java.net.URLEncoder.encode(it, "UTF-8") }.orEmpty()
            val page = api(origin).call("GET", "/api/v1/tasks?limit=10$suffix"); val array = page.getJSONArray("tasks")
            val log = ActivityStore(this, origin.gateway)
            val fetched = (0 until array.length()).map { log.entryFrom(array.getJSONObject(it)) }
            val next = if (page.isNull("next_cursor")) null else page.getString("next_cursor")
            ui(ticket) {
                olderEntries.addAll(fetched.filter { f -> olderEntries.none { it.taskId == f.taskId } })
                olderCursor = next; olderExhausted = next == null
                renderActivity()
            }
        }
    }
    private fun showPage(index: Int) {
        if (index == 0) dismissSheet()
        else openSheet(index, if (index == 1) "Computers & audio" else "Settings")
        if (index == 1) autoDiscover()
    }
    private fun openSheet(index: Int, title: String) {
        dismissSheet()
        chatOpen = index == 0
        inDialog = !chatOpen; waitingForRoute = false; handler.removeCallbacks(routeTimeout); handler.removeCallbacks(listenAgain)
        if (::speech.isInitialized && !chatOpen) speech.cancelRecognition()
        val page = pages[index]
        (page.parent as? ViewGroup)?.removeView(page)
        lateinit var dialog: android.app.Dialog
        dialog = bottomSheet(title, page, tall = true) {
            if (sheet === dialog) {
                (page.parent as? ViewGroup)?.removeView(page)
                sheet = null; chatOpen = false; inDialog = audioPicker?.isShowing == true || overlay?.isShowing == true; scheduleListening()
            }
        }
        sheet = dialog
        showSheet(dialog, tall = true)
    }
    private fun setPhase(requested: VoicePhase) {
        // The wake-word listener gives the microphone to the conversation and takes it back when idle.
        WakeWordService.hold(requested == VoicePhase.LISTENING || requested == VoicePhase.SPEAKING ||
            (requested == VoicePhase.THINKING && ::speech.isInitialized && speech.isListening) || (noteMode && !notePaused))
        if (!::avatar.isInitialized || isDestroyed) return
        val phase = if (requested == VoicePhase.IDLE && working) VoicePhase.THINKING else requested
        // Keep the screen on through a spoken turn (it matters over the lock screen, whose timeout is short).
        if (phase == VoicePhase.IDLE) window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        else window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        avatar.phase = phase
        phaseLabel.alpha = 1f
        phaseLabel.text = when (phase) {
            VoicePhase.IDLE -> currentSuggestion(); VoicePhase.LISTENING -> "I'm listening…"
            VoicePhase.THINKING -> if (working) "${selectedComputer?.name ?: "Your computer"} is working on it…" else "One moment…"
            VoicePhase.SPEAKING -> "Here's what I found"
        }
        // The round button shows one of three states: speak (mic), listening (pause = finish), speaking (stop).
        // In note mode it is steady: "recording" (pause glyph) or "paused" (mic glyph), never the per-phrase churn.
        val speaking = phase == VoicePhase.SPEAKING
        val listening = if (noteMode && !speaking) !notePaused else phase == VoicePhase.LISTENING
        mic.setImageResource(when { listening -> android.R.drawable.ic_media_pause; speaking -> android.R.drawable.ic_lock_silent_mode; else -> android.R.drawable.ic_btn_speak_now })
        mic.imageTintList = android.content.res.ColorStateList.valueOf(if (listening || speaking) base else ink)
        mic.background = when {
            listening -> shape(accent, 36)
            speaking -> shape(Color.rgb(226, 190, 255), 36)
            noteMode -> shape(surface, 36, accent)
            else -> shape(surface, 36, Color.rgb(72, 48, 109))
        }
        mic.contentDescription = when {
            noteMode && !notePaused -> "Recording your note. Tap to pause."
            noteMode -> "Note paused. Tap to continue."
            listening -> "Finish speaking"; speaking -> "Stop speaking"; else -> "Tap to speak"
        }
        if (noteMode && phase != VoicePhase.SPEAKING) phaseLabel.text = "Taking a note · tap ✎ when you're done"
    }
    private fun currentSuggestion(): String = "Try: “${suggestions[suggestionIndex]}”"
    private fun greetingWord(): String = when (java.util.Calendar.getInstance().get(java.util.Calendar.HOUR_OF_DAY)) {
        in 5..11 -> "Good morning"; in 12..16 -> "Good afternoon"; in 17..21 -> "Good evening"; else -> "Still up?"
    }
    private fun greetingText(): String = greetingWord() + (city?.let { "  ·  $it" } ?: "")
    private fun spokenGreeting(): String {
        val place = city?.let { " from $it" } ?: ""
        val computer = when {
            selectedComputer == null -> "No computer is paired yet. Scan a QR code when you're ready."
            connected -> "${selectedComputer!!.name} is online" + (store.choice("profile").takeIf { it == "desktop" || it == "develop" }?.let { " in $it mode" } ?: "") + "."
            else -> "${selectedComputer!!.name} is saved. I'll connect when you speak."
        }
        return "${greetingWord()}$place. $computer Try saying: ${suggestions[suggestionIndex]}."
    }
    /** Coarse location only, for a human greeting; never sent anywhere. */
    private fun fetchCity() {
        if (checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) != PackageManager.PERMISSION_GRANTED) return
        val manager = getSystemService(android.location.LocationManager::class.java) ?: return
        val providers = listOf(android.location.LocationManager.NETWORK_PROVIDER, android.location.LocationManager.PASSIVE_PROVIDER, android.location.LocationManager.GPS_PROVIDER)
        val last = providers.mapNotNull { try { if (manager.isProviderEnabled(it)) manager.getLastKnownLocation(it) else null } catch (_: Exception) { null } }
            .maxByOrNull { it.time }
        if (last != null) geocode(last)
        else try {
            manager.getCurrentLocation(android.location.LocationManager.NETWORK_PROVIDER, null, mainExecutor) { fix -> fix?.let(::geocode) }
        } catch (_: Exception) { }
    }
    private fun geocode(fix: android.location.Location) {
        executor.execute {
            val name = try {
                @Suppress("DEPRECATION")
                android.location.Geocoder(this, java.util.Locale.getDefault()).getFromLocation(fix.latitude, fix.longitude, 1)
                    ?.firstOrNull()?.let { it.locality ?: it.subAdminArea ?: it.adminArea }
            } catch (_: Exception) { null }
            handler.post { if (!isDestroyed && !name.isNullOrBlank()) { city = name; greetingLabel.text = greetingText() } }
        }
    }
    private fun showStatus(text: String) { if (!isDestroyed && ::status.isInitialized) status.text = text }
    private fun scheduleListening(delay: Long = 700) {
        handler.removeCallbacks(listenAgain)
        val reply = pendingReply
        if (reply != null && foreground && !inDialog && !busy && !speech.isListening && !speech.isSpeaking) {
            pendingReply = null
            if (autoSpeech.isChecked && selectedTask()?.optString("task_id") == reply.first) {
                speak(reply.second); return
            }
        }
        if (foreground && handsFree.isChecked) handler.postDelayed(listenAgain, delay)
    }
    private fun requestListen() {
        android.util.Log.i("Chintu", "listen requested: note=$noteMode listening=${speech.isListening} busy=$busy dialog=$inDialog")
        if (!foreground || inDialog) return
        // One attempt at a time: every restart path funnels through here, so rate-limit them together.
        val now = System.currentTimeMillis()
        if (speech.isListening || now - lastListenAttempt < 1200) return
        lastListenAttempt = now
        if (busy) { showStatus("One moment while I finish connecting."); scheduleListening(); return }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 1); return
        }
        val tag = language.text.toString().trim()
        if (tag.isBlank()) { showStatus("Choose a speech language in Settings."); handsFree.isChecked = false; return }
        audio.resume()
        if (!audio.routeReady()) {
            waitingForRoute = true; handler.removeCallbacks(routeTimeout); handler.postDelayed(routeTimeout, 5000)
            showStatus("Waiting for the audio device to connect…"); return
        }
        waitingForRoute = false; handler.removeCallbacks(routeTimeout)
        speech.listen(online.isChecked, tag, dictation = noteMode && !notePaused)
    }
    /** The recogniser's start/stop tones play on the media/system streams; silence them while a note records. */
    private var tonesMuted = false
    private fun muteRecognizerTones(mute: Boolean) {
        if (mute == tonesMuted) return
        val manager = getSystemService(android.media.AudioManager::class.java) ?: return
        val action = if (mute) android.media.AudioManager.ADJUST_MUTE else android.media.AudioManager.ADJUST_UNMUTE
        for (stream in listOf(android.media.AudioManager.STREAM_MUSIC, android.media.AudioManager.STREAM_SYSTEM, android.media.AudioManager.STREAM_NOTIFICATION)) {
            try { manager.adjustStreamVolume(stream, action, 0) } catch (_: Exception) { }
        }
        tonesMuted = mute
    }
    private fun requestBluetooth() {
        if (checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(arrayOf(Manifest.permission.BLUETOOTH_CONNECT), 2)
        else { audio.resume(); audioChanged() }
    }
    private fun showAudioPicker() {
        if (checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED) {
            openAudioAfterPermission = true; requestBluetooth(); return
        }
        openAudioPicker()
    }
    private fun openAudioPicker() {
        audioPicker?.dismiss()
        inDialog = true; waitingForRoute = false; handler.removeCallbacks(routeTimeout); handler.removeCallbacks(listenAgain)
        speech.cancelRecognition()
        val body = column()
        label(body, "Only devices with both a microphone and a speaker are listed.", 13f, muted).setPadding(dp(20), 0, dp(20), dp(8))
        val list = column(body, 12)
        lateinit var dialog: android.app.Dialog
        dialog = bottomSheet("Audio device", body) {
            if (audioPicker === dialog) { audioPicker = null; audioPickerRefresh = null }
            inDialog = sheet?.isShowing == true && !chatOpen; scheduleListening()
        }
        fun updateList() {
            list.removeAllViews()
            val routes = audio.routes()
            if (routes.isEmpty()) label(list, "No voice device available. Connect a headset or allow Nearby devices.", 14f, muted)
            routes.forEach { route ->
                val row = LinearLayout(this).apply {
                    orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
                    setPadding(dp(18), dp(14), dp(18), dp(14))
                    background = shape(surface, 18, if (route.selected) accent else null)
                    isClickable = true; isFocusable = true
                    contentDescription = route.label + if (route.selected) ", selected" else ""
                    setOnClickListener { speech.pause(); audio.select(route.id); dialog.dismiss() }
                }
                row.addView(TextView(this).apply {
                    text = if (route.bluetooth) "ᛒ" else "☏"; textSize = 20f; gravity = Gravity.CENTER
                    setTextColor(if (route.selected) accent else muted)
                }, LinearLayout.LayoutParams(dp(32), -2).apply { marginEnd = dp(12) })
                val labels = column().apply {
                    label(this, route.label, 16f, ink, route.selected)
                    label(this, route.detail, 12f, if (route.selected) accent else muted)
                }
                row.addView(labels, LinearLayout.LayoutParams(0, -2, 1f))
                row.addView(TextView(this).apply {
                    text = if (route.selected) "●" else "○"; textSize = 18f; setTextColor(if (route.selected) accent else muted)
                }, LinearLayout.LayoutParams(-2, -2))
                list.addView(row, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(8) })
            }
            gap(list, 4)
            button(list, "Bluetooth settings") { startActivity(Intent(Settings.ACTION_BLUETOOTH_SETTINGS)) }
        }
        audioPicker = dialog
        audioPickerRefresh = ::updateList
        showSheet(dialog); updateList()
    }
    private var audioPickerRefresh: (() -> Unit)? = null
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent); setIntent(intent)
        if (handleWakeIntent(intent) && foreground) { wakeLaunch = false; onWakeWord() }
    }
    /** The wake-word service opened the app: show it over the lock screen and listen once visible. */
    private fun handleWakeIntent(intent: Intent?): Boolean {
        if (intent?.action != WakeWordService.ACTION_WAKE) return false
        wakeLaunch = true; setShowWhenLocked(true); setTurnScreenOn(true)
        return true
    }
    private fun onWakeWord(attempt: Int = 0) {
        if (!foreground) return
        speech.stopSpeaking(); lastListenAttempt = 0L
        if (noteMode) { showStatus("Your note is open. Tap ✎ to finish it first."); WakeWordService.hold(false); return }
        // Opening the app from the background reconnects to the computer first; listen as soon as that is done
        // instead of relying on the hands-free retry, which only runs while hands-free is on.
        if (busy && attempt < 20) { showStatus("One moment, connecting…"); handler.postDelayed({ onWakeWord(attempt + 1) }, 400); return }
        requestListen()
        // If listening could not start (dialog open, no permission), give the microphone back.
        handler.postDelayed({ if (!speech.isListening && !speech.isSpeaking) WakeWordService.hold(false) }, 4000)
    }
    private fun enableWakeWord() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 4); return
        }
        if (android.os.Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 5)
        preferences.edit().putBoolean(WakeWordService.PREF_ENABLED, true).apply()
        WakeWordService.start(this)
        showStatus("Say \u201cChintu\u201d, wait for the chime, then speak.")
        // Stay alive in the background, and allow opening over the lock screen when the wake word is heard.
        val power = getSystemService(android.os.PowerManager::class.java)
        if (power != null && !power.isIgnoringBatteryOptimizations(packageName)) {
            try { startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, android.net.Uri.parse("package:$packageName"))) }
            catch (_: Exception) { }
        }
        if (!Settings.canDrawOverlays(this)) {
            showStatus("Allow \u201cDisplay over other apps\u201d so \u201cChintu\u201d can open the app from the background.")
            try { startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, android.net.Uri.parse("package:$packageName"))) }
            catch (_: Exception) { }
        }
    }
    override fun onRequestPermissionsResult(code: Int, permissions: Array<out String>, results: IntArray) {
        super.onRequestPermissionsResult(code, permissions, results)
        if (!foreground) return
        if (code == 1) {
            if (results.firstOrNull() == PackageManager.PERMISSION_GRANTED) requestListen()
            else { handsFree.isChecked = false; showStatus("You can type instructions while microphone access is off.") }
        } else if (code == 2) {
            audio.resume(); audioChanged()
            if (openAudioAfterPermission) { openAudioAfterPermission = false; openAudioPicker() }
        } else if (code == 3) {
            if (results.firstOrNull() == PackageManager.PERMISSION_GRANTED) fetchCity()
        } else if (code == 4) {
            if (results.firstOrNull() == PackageManager.PERMISSION_GRANTED) enableWakeWord()
            else { wakeWord.isChecked = false; showStatus("The wake word needs microphone access.") }
        }
    }
    private fun audioChanged() {
        if (!::audio.isInitialized || isDestroyed) return
        val current = audio.currentRouteLabel()
        val activeId = audio.routes().firstOrNull { it.selected }?.id
        if (activeId != lastAudioId && ::speech.isInitialized && speech.isListening) {
            speech.cancelRecognition(); showStatus("Audio changed to $current. Please repeat your instruction."); scheduleListening(1000)
        }
        lastAudioId = activeId
        if (waitingForRoute && foreground && audio.routeReady()) {
            waitingForRoute = false; handler.removeCallbacks(routeTimeout)
            handler.post { if (foreground && !inDialog && !busy && !speech.isListening && !speech.isSpeaking) requestListen() }
        }
        audioLabel.text = current
        audioButton.setImageResource(if (audio.isBluetoothActive()) android.R.drawable.stat_sys_data_bluetooth else android.R.drawable.ic_lock_silent_mode_off)
        audioButton.contentDescription = "Audio: $current. Switch device"
        audioButton.imageTintList = android.content.res.ColorStateList.valueOf(if (audio.isBluetoothActive()) accent else muted)
        audioPickerRefresh?.invoke()
        audioList.removeAllViews()
        audio.routes().forEach { route ->
            gap(audioList, 8)
            val row = button(audioList, (if (route.selected) "✓  " else "") + route.label, route.selected) {
                speech.pause()
                if (audio.select(route.id)) showStatus("Switching audio to ${route.label}…")
                scheduleListening(1200)
            }
            row.isEnabled = route.selectable; row.alpha = if (route.selectable) 1f else .55f
            label(audioList, route.detail, 12f, muted)
        }
        if (checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED)
            label(audioList, "Tap the audio button and allow Nearby devices to use Bluetooth.", 12f, muted)
    }
    private fun onUtterance(text: String) {
        autoRetries = 0
        if (noteMode) { flushNote(text, final = true); handler.postDelayed({ if (noteMode && !notePaused && foreground && !inDialog && !speech.isListening) requestListen() }, 400); return }
        val command = VoiceCommand.parse(text)
        android.util.Log.i("Chintu", "utterance: kind=${command.kind()} words=${text.trim().split(Regex("\\s+")).size} " +
            "pending=${guard.pending() != null} computer=${selectedComputer != null} connected=$connected autoSend=${autoSend.isChecked}")
        when (command.kind()) {
            VoiceCommand.Kind.SWITCH_DESTINATION -> switchByName(command.destinationName())
            VoiceCommand.Kind.NEW_CONVERSATION -> startChatByVoice(command.folderName())
            VoiceCommand.Kind.UNKNOWN_COMMAND -> respond("Try “Chintu, new chat”, or “Chintu, switch to” followed by a saved computer name.")
            VoiceCommand.Kind.TASK -> {
                if (guard.pending() != null) {
                    reviewPending = true
                    respond("This computer has an instruction waiting for a receipt. Tap Retry, or ask me to switch computers first.")
                    return
                }
                setTranscript(text); store.draft = text
                if (selectedComputer == null) {
                    // Keep the conversation alive: say what was heard and what is needed, instead of a silent sheet.
                    reviewPending = true
                    val only = computers.all().singleOrNull()
                    if (only != null) connect(only, true) else respond(sleepingLine())
                    return
                }
                if (!connected && !ClientStore(this, selectedComputer!!.url).isPaired()) {
                    reviewPending = true; connect(selectedComputer!!, true); return
                }
                if (autoSend.isChecked) submit() else {
                    reviewPending = true; showStatus("Ready to send${selectedComputer?.let { " to ${it.name}" }.orEmpty()}."); showConversation()
                }
            }
        }
        scheduleListening()
    }
    private fun handleTypedSend() {
        val text = transcript.text.toString().trim()
        if (text.isEmpty() && guard.pending() == null) { showStatus("Speak or type an instruction first."); return }
        if (guard.pending() != null) { submit(); return }
        val parsed = VoiceCommand.parse(text)
        if (parsed.kind() == VoiceCommand.Kind.TASK) submit()
        else onUtterance(text)
    }
    private fun setTranscript(text: String) {
        settingTranscript = true
        try { transcript.setText(text) } finally { settingTranscript = false }
    }
    private fun switchByName(name: String) {
        val list = computers.all().map { DestinationMatcher.Destination(it.url, it.name, it.aliases) }
        val match = DestinationMatcher.match(name, list)
        when (match.status()) {
            DestinationMatcher.Status.MATCHED -> computers.all().firstOrNull { it.url == match.destination().id() }?.let { connect(it, true) }
            DestinationMatcher.Status.AMBIGUOUS -> respond("More than one computer has that name. Choose one using the computer selector, or give it a unique alias.")
            DestinationMatcher.Status.NOT_FOUND -> respond("I don't have a computer named $name yet. Add it using the computer selector first.")
        }
    }
    /** Spoken when an instruction arrives but no computer is paired. Rotates so repeats stay fresh. */
    private val sleepingLines = listOf(
        "Sorry, Chintu is sleeping. None of your computers are plugged into my brain yet. Connect one and wake me up.",
        "I heard you, but I'm a ghost without a machine. Pair a computer and I'll get to work.",
        "Chintu is napping. No computer is connected, so there's nowhere to send that. Add one from the computer selector.",
        "That sounded important, but I'm not connected to any computer right now. Say Chintu, connect me to, and a saved computer name.",
        "All dressed up and no computer to go to. Pair one and I'll stop sulking.",
        "I'd love to help, but my hands are on another machine, and that machine isn't connected yet.",
        "Chintu is offline duty. Connect a computer through Tailscale and I'll spring back to life.",
        "Message received, destination missing. I'm a courier with no address. Add a computer first.",
        "I'm here, but your computers are not. Wake one up and say Chintu, connect me to it.",
        "No computer, no magic. Pair your Mac or your Ubuntu box and try that again.",
    )
    private fun sleepingLine(): String = sleepingLines.random()
    private fun respond(text: String) { output.text = text; lastReply = text; showStatus(text); if (!::quiet.isInitialized || !quiet.isChecked) speak(text) }
    private fun speak(text: String) {
        if (text.isBlank()) { showStatus("There's nothing to read yet."); return }
        handler.removeCallbacks(listenAgain)
        if (foreground && !speech.speak(text, language.text.toString())) scheduleListening()
    }
    private fun updateDestination() {
        val saved = computers.all().size
        destinationLabel.text = when {
            selectedComputer != null -> (if (connected) "●  " else "○  ") + selectedComputer!!.name +
                (if (saved > 1) "  ·  1 of $saved computers" else "") + "  ›"
            saved > 0 -> "Choose a computer  ·  $saved saved  ›"
            else -> "Add a computer  ›"
        }
        destinationLabel.setTextColor(if (connected) accent else muted)
        connectionLabel.text = if (connected) "Connected · all new instructions go here" else if (selectedComputer != null) "Saved · reconnect to verify availability" else "Tap to connect through Tailscale"
        connectionLabel.setTextColor(if (connected) accent else muted)
        val pending = guard.pending() != null
        send.text = if (pending) "↻" else "➤"
        send.contentDescription = if (pending) "Retry on ${selectedComputer?.name ?: "computer"}" else "Send${selectedComputer?.let { " to ${it.name}" }.orEmpty()}"
        updateChatHeader()
    }
    private fun renderComputers() {
        computerList.removeAllViews()
        computers.all().forEach { computer ->
            gap(computerList)
            button(computerList, (if (computer.url == selectedComputer?.url) "✓  " else "") + computer.name,
                computer.url == selectedComputer?.url) { connect(computer, true) }
            label(computerList, java.net.URI(computer.url).host, 12f, muted)
            label(computerList, "Edit name, voice aliases & pairing", 13f, accent).apply {
                minHeight = dp(48); gravity = Gravity.CENTER_VERTICAL; setOnClickListener { showComputerDialog(computer) }
            }
        }
    }
    private fun showComputerDialog(existing: Computer?, discoveredUrl: String = "", discoveredName: String = "") {
        if (busy) { showStatus("Wait for the current request before editing a computer."); return }
        inDialog = true; waitingForRoute = false; handler.removeCallbacks(routeTimeout)
        speech.pause(); handler.removeCallbacks(listenAgain)
        val body = column(padding = 20)
        label(body, "A name you can say", 13f, muted)
        val name = field(body, "Cobra Ubuntu", existing?.name ?: discoveredName, true); gap(body)
        val aliases = field(body, "Voice aliases, separated by commas", existing?.aliases.orEmpty(), true); gap(body)
        val url = field(body, "https://computer.tailnet.ts.net", existing?.url ?: discoveredUrl, true).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            if (existing != null) isEnabled = false
        }; gap(body)
        val secret = field(body, "Enrollment secret (first pairing)", single = true).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            typeface = Typeface.DEFAULT
        }
        label(body, "Run Chintu on this computer and use its enrollment secret. Saved paired computers don't need a new secret.", 12f, muted)
        val error = label(body, "", 13f, Color.rgb(255, 179, 157)); gap(body)
        if (existing == null || !ClientStore(this, existing.url).isPaired()) {
            button(body, "Scan the QR code shown by the computer") {
                scanPairingCode({ code ->
                    if (existing == null) { name.setText(code.name); url.setText(code.url) }
                    else if (code.url != existing.url) { error.text = "That code belongs to a different computer (${java.net.URI(code.url).host})."; return@scanPairingCode }
                    secret.setText(code.secret)
                    if (aliases.text.isBlank()) aliases.setText(code.aliases)
                    error.text = ""
                }, { message -> error.text = message })
            }
            gap(body)
        }
        val actions = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        body.addView(actions, LinearLayout.LayoutParams(-1, -2))
        val scroller = ScrollView(this).apply { isFillViewport = false; addView(body) }
        val dialog = bottomSheet(if (existing == null) "Add a computer" else existing.name, scroller) {
            inDialog = sheet?.isShowing == true && !chatOpen; scheduleListening()
        }
        if (existing != null) {
            gap(body, 14)
            val remove = button(body, "Remove this computer") { confirmRemoval(existing, dialog) }
            remove.setTextColor(Color.rgb(255, 179, 157)); gap(body, 8)
        }
        val cancel = button(actions, "Cancel") { dialog.dismiss() }
        (cancel.layoutParams as? LinearLayout.LayoutParams ?: LinearLayout.LayoutParams(0, -2, 1f)).let {
            cancel.layoutParams = LinearLayout.LayoutParams(0, -2, 1f).apply { marginEnd = dp(8) }
        }
        val save = button(actions, "Save & connect", true) {}
        save.layoutParams = LinearLayout.LayoutParams(0, -2, 1f)
        run {
            save.setOnClickListener {
                try {
                    val target = Computer(GatewayApi.validateBase(url.text.toString()), name.text.toString().trim(), aliases.text.toString().trim())
                    val enrollment = secret.text.toString().trim()
                    require(enrollment.isEmpty() || enrollment.length in 16..512) { "Enter a valid enrollment secret from this computer." }
                    val candidate = ClientStore(this, target.url)
                    require(enrollment.isEmpty() || candidate.read() == null) { "Retry this computer's pending instruction before pairing again." }
                    computers.save(target); renderComputers(); secret.setText(""); dialog.dismiss()
                    if (enrollment.isEmpty()) connect(target, true)
                    else background("Pairing with ${target.name}…") { ticket ->
                        val response = GatewayApi(target.url).call("POST", "/api/v1/pair", JSONObject()
                            .put("enrollment_secret", enrollment).put("client_name", "Chintu Android").toString())
                        candidate.paired(response.getString("client_id"), response.getString("token"))
                        val catalog = loadCatalog(candidate)
                        ui(ticket) { activate(target, candidate, catalog, true) }
                    }
                } catch (failure: Exception) { error.text = failure.message ?: "Check the computer details." }
            }
        }
        showSheet(dialog)
    }
    private fun confirmRemoval(target: Computer, editor: android.app.Dialog) {
        val body = column(padding = 20)
        label(body, "Remove ${target.name}? Its pairing, saved choices and any waiting instruction on this phone are deleted. Tasks already running on the computer keep running.", 15f, ink)
        gap(body)
        val actions = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        body.addView(actions, LinearLayout.LayoutParams(-1, -2))
        val dialog = bottomSheet("Remove computer", body)
        button(actions, "Keep") { dialog.dismiss() }.layoutParams = LinearLayout.LayoutParams(0, -2, 1f).apply { marginEnd = dp(8) }
        button(actions, "Remove", true) { dialog.dismiss(); editor.dismiss(); removeComputer(target) }.layoutParams = LinearLayout.LayoutParams(0, -2, 1f)
        showSheet(dialog)
    }
    private fun removeComputer(target: Computer) {
        val client = ClientStore(this, target.url)
        val wasActive = selectedComputer?.url == target.url
        if (wasActive) {
            epoch++; polling = false; handler.removeCallbacks(poll); pendingReply = null; stopStream()
            connected = false; selectedComputer = null
            store = ClientStore(this, ""); guard = SubmissionGuard(store); cursor = EventCursor(store.cursor)
            projects = emptyList(); capabilities = emptyList(); tasks = emptyList()
            rebuildingSelections = true
            setOptions(project, listOf("Choose workspace")); setOptions(provider, listOf("Choose agent")); setOptions(profile, listOf("Choose permission profile"))
            setOptions(task, listOf("Choose a task")); rebuildingSelections = false
            discovered = emptyList(); discoveryList.removeAllViews()
            discoveryStatus.text = "Pair one computer and the others appear here automatically."
        }
        computers.remove(target.url)
        // Best effort: tell the gateway to revoke this phone before the credential is dropped.
        val token = if (client.isPaired()) try { client.token() } catch (_: Exception) { null } else null
        executor.execute {
            if (token != null) try { GatewayApi(target.url, token).call("DELETE", "/api/v1/clients/me") } catch (_: Exception) { }
            client.forget()
        }
        renderComputers(); updateDestination()
        showStatus("Removed ${target.name}.")
    }
    private data class PairingCode(val url: String, val name: String, val secret: String, val aliases: String)
    /** Chintu QR payload: {"chintu":1,"url":"https://host.tailnet.ts.net","name":"My Mac","aliases":"mac","secret":"…"}. */
    private fun parsePairingCode(raw: String): PairingCode {
        val json = try { JSONObject(raw) } catch (_: Exception) { throw IllegalArgumentException("That QR code isn't a Chintu pairing code.") }
        require(json.optInt("chintu") == 1) { "That QR code isn't a Chintu pairing code." }
        val url = GatewayApi.validateBase(json.optString("url"))
        val secret = json.optString("secret").trim()
        require(secret.length in 16..512) { "This pairing code has expired or is invalid. Show a fresh one on the computer." }
        return PairingCode(url, json.optString("name").trim(), secret, json.optString("aliases").trim())
    }
    /** Uses Google's code scanner, which runs in its own system UI and needs no camera permission here. */
    private fun scanPairingCode(onCode: (PairingCode) -> Unit, onError: (String) -> Unit) {
        val options = com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions.Builder()
            .setBarcodeFormats(com.google.mlkit.vision.barcode.common.Barcode.FORMAT_QR_CODE).build()
        com.google.mlkit.vision.codescanner.GmsBarcodeScanning.getClient(this, options).startScan()
            .addOnSuccessListener { barcode ->
                try { onCode(parsePairingCode(barcode.rawValue.orEmpty())) }
                catch (failure: Exception) { onError(failure.message ?: "Couldn't read that QR code.") }
            }
            .addOnCanceledListener { }
            .addOnFailureListener { failure ->
                android.util.Log.w("Chintu", "QR scan failed", failure)
                onError("Couldn't scan: ${failure.message ?: failure.javaClass.simpleName}. Fill the frame with the code, or enter the details manually.")
            }
    }
    private data class Catalog(val projects: List<JSONObject>, val providers: List<JSONObject>, val tasks: List<JSONObject>)
    private fun loadCatalog(target: ClientStore): Catalog {
        val client = api(target)
        val p = client.call("GET", "/api/v1/projects").getJSONArray("projects")
        val c = client.call("GET", "/api/v1/capabilities").getJSONArray("providers")
        return Catalog((0 until p.length()).map { p.getJSONObject(it) },
            (0 until c.length()).map { c.getJSONObject(it) }.filter { it.getBoolean("available") }, loadTasks(client))
    }
    private fun connect(target: Computer, announce: Boolean) {
        if (busy) { showStatus("One request is still in progress. I'll keep the current destination until it finishes."); scheduleListening(); return }
        val candidate = ClientStore(this, target.url)
        if (!candidate.isPaired()) {
            respond("${target.name} is saved but not paired yet. Scan the QR code shown on that computer to finish pairing.")
            // Offer the pairing form once per session; after that just say it, don't keep taking over the screen.
            if (pairingPrompted.add(target.url)) showComputerDialog(target)
            return
        }
        waitingForRoute = false; handler.removeCallbacks(routeTimeout)
        speech.pause(); handler.removeCallbacks(listenAgain)
        background("Connecting to ${target.name}…") { ticket ->
            val catalog = loadCatalog(candidate)
            ui(ticket) { activate(target, candidate, catalog, announce) }
        }
    }
    private fun activate(target: Computer, candidate: ClientStore, catalog: Catalog, announce: Boolean) {
        val previousDraft = transcript.text.toString()
        store.draft = if (previousDraft.isNotBlank() && VoiceCommand.parse(previousDraft).kind() != VoiceCommand.Kind.TASK) "" else previousDraft
        computers.activate(target.url)
        epoch++; polling = false; handler.removeCallbacks(poll)
        pendingReply = null; discovered = emptyList(); discoveryList.removeAllViews()
        discoveryStatus.text = "Looking for other computers visible from ${target.name}…"
        handler.postDelayed({ autoDiscover() }, 300)
        store = candidate; selectedComputer = target; guard = SubmissionGuard(store); cursor = EventCursor(store.cursor)
        cursor.reconnect(); connected = true; projects = catalog.projects; capabilities = catalog.providers; tasks = emptyList()
        chatTurns.clear(); chatEvents.clear(); chatExpanded.clear()
        setTranscript(store.draft); reviewPending = store.draft.isNotBlank()
        lastReply = ""; output.text = "Connected to ${target.name}. Ready for your next instruction."
        rebuildingSelections = true
        setOptions(project, listOf("Choose workspace") + projects.map { it.getString("display_name") })
        setOptions(provider, listOf("Choose agent") + capabilities.map { if (it.getString("provider") == "scripted") "Scripted demo" else it.getString("provider") })
        // Remembered choices win; otherwise pick the obvious defaults so speaking works without a settings trip.
        project.setSelection((projects.indexOfFirst { it.getString("project_id") == store.choice("project") }.takeIf { it >= 0 }
            ?: projects.indexOfFirst { it.getString("project_id") == "home" }.takeIf { it >= 0 } ?: (if (projects.isEmpty()) -1 else 0)) + 1)
        provider.setSelection((capabilities.indexOfFirst { it.getString("provider") == store.choice("provider") }.takeIf { it >= 0 }
            ?: capabilities.indexOfFirst { it.getString("provider") != "scripted" }.takeIf { it >= 0 } ?: (if (capabilities.isEmpty()) -1 else 0)) + 1)
        updateProfiles(); rebuildingSelections = false; replaceTasks(catalog.tasks)
        updateDestination(); renderComputers(); persistChoices(); updateActivityBadge()
        polling = true; if (foreground) handler.post(poll)
        startStream()
        if (chatOpen) { renderChat(); handler.post { loadChat() } }
        val message = "${target.name} is connected. All new instructions will go there now."
        showStatus(message)
        if (announce) { dismissSheet(); respond(message) }
    }
    private val pairingPrompted = mutableSetOf<String>()
    private var autoRetries = 0
    private var lastListenAttempt = 0L
    /** Delay before another microphone attempt: longer when the speech service itself was the problem. */
    private fun retryDelay(): Long = if (speech.lastError == android.speech.SpeechRecognizer.ERROR_RECOGNIZER_BUSY ||
        speech.lastError == android.speech.SpeechRecognizer.ERROR_CLIENT ||
        speech.lastError == android.speech.SpeechRecognizer.ERROR_SERVER_DISCONNECTED) 1800L else 600L
    /** True from task acceptance until its terminal event; keeps the orb in its working state between phases. */
    private var working = false
    private var workingInstruction = ""
    private var lastUpdateSpokenAt = 0L
    private var lastProgressAt = 0L
    private var lastSpokenUpdate = ""
    private val fillers = listOf("Still on it.", "Give me a few more seconds.", "Almost there, checking one more thing.",
        "Working through it.", "Hang on, nearly done.")
    private val openers = listOf("", "", "Okay. ", "Next, ", "Right now, ", "Now ")
    /** Turns a raw daemon progress line into something short enough to say between tool calls. */
    private fun conversational(kind: String, text: String): String? {
        val line = text.trim(); if (line.isBlank()) return null
        if (kind == "output") return line.split(Regex("(?<=[.!?])\\s+")).firstOrNull()?.take(160)
        val value = line.substringAfter(": ", "").trim()
        return when {
            line.startsWith("Running:") -> {
                val head = value.split(Regex("[;&|]"))[0].trim().split(" ").filter { it.isNotBlank() }
                val tool = head.firstOrNull { it != "cd" && !it.startsWith("~") && !it.startsWith("/") && !it.contains("=") } ?: "a command"
                "running $tool on the computer"
            }
            line.startsWith("Reading:") -> "reading ${value.substringAfterLast('/')}"
            line.startsWith("Editing:") || line.startsWith("Writing:") -> "editing ${value.substringAfterLast('/')}"
            line.startsWith("Finding:") || line.startsWith("Searching:") -> "searching your files"
            line.startsWith("Using: mcp__") -> {
                val server = value.removePrefix("mcp__").substringBefore("__").replace("claude_ai_", "").replace('_', ' ').trim()
                "checking $server"
            }
            line.startsWith("Using:") -> "looking for the right tool"
            line.startsWith("Denied") -> "skipping a step that needs your approval"
            else -> null
        }
    }
    private fun maybeNarrate(updates: List<Pair<String, String>>) {
        if (!working || !speakingAllowed() || !foreground || inDialog || speech.isListening || speech.isSpeaking) return
        val now = System.currentTimeMillis()
        if (updates.isNotEmpty()) lastProgressAt = now
        val fresh = updates.lastOrNull { it.first == "output" } ?: updates.lastOrNull()
        val phrase = fresh?.let { conversational(it.first, it.second) }
        when {
            phrase != null && phrase != lastSpokenUpdate && now - lastUpdateSpokenAt > 12_000 -> {
                lastSpokenUpdate = phrase; lastUpdateSpokenAt = now
                speech.speak(if (fresh.first == "output") phrase else openers.random() + phrase + ".", language.text.toString())
            }
            now - lastUpdateSpokenAt > 40_000 && now - lastProgressAt > 20_000 -> {
                lastUpdateSpokenAt = now; speech.speak(fillers.random(), language.text.toString())
            }
        }
    }
    private var discoveredAt = 0L
    /** Runs discovery by itself after a connection or when the computers sheet opens; stays silent on the home screen. */
    private fun autoDiscover() {
        if (!foreground || busy || !store.isPaired()) return
        if (System.currentTimeMillis() - discoveredAt < 60_000 && discovered.isNotEmpty()) return
        discover(quiet = true)
    }
    private fun discover(quiet: Boolean = false) {
        if (!store.isPaired()) { if (!quiet) showStatus("Add and pair your first computer, then find its Tailscale peers."); return }
        val origin = store
        discoveryStatus.text = "Looking for computers visible to ${selectedComputer?.name}…"
        background(if (quiet) null else "Finding computers in Tailscale…") { ticket ->
            val result = api(origin).call("GET", "/api/v1/systems")
            val array = result.getJSONArray("systems")
            val found = (0 until array.length()).map { array.getJSONObject(it) }
            ui(ticket) {
                discovered = found; discoveredAt = System.currentTimeMillis(); discoveryList.removeAllViews()
                val saved = computers.all().map { it.url }.toSet()
                val fresh = found.filter { GatewayApi.validateBase(it.getString("gateway_url")) !in saved }
                discoveryStatus.text = when {
                    result.getString("status") != "available" -> "Tailscale discovery is unavailable on this computer. You can still add a private URL manually."
                    fresh.isEmpty() -> "Every computer on your network is already paired."
                    else -> "Tap a computer to pair it. Each one needs Chintu running."
                }
                fresh.forEach { value ->
                    gap(discoveryList, 8)
                    val url = GatewayApi.validateBase(value.getString("gateway_url"))
                    val online = value.getBoolean("online")
                    val row = LinearLayout(this).apply {
                        orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
                        setPadding(dp(16), dp(12), dp(16), dp(12)); background = shape(base, 16, Color.rgb(60, 46, 94))
                        isClickable = true; isFocusable = true
                        setOnClickListener { showComputerDialog(null, url, value.getString("name")) }
                    }
                    row.addView(TextView(this).apply { text = if (online) "●" else "○"; textSize = 14f; setTextColor(if (online) Color.rgb(140, 230, 170) else muted) },
                        LinearLayout.LayoutParams(dp(24), -2))
                    val labels = column().apply {
                        label(this, value.getString("name"), 15f, ink, true)
                        label(this, if (online) "Online · tap to pair" else "Offline · tap to pair later", 12f, muted)
                    }
                    row.addView(labels, LinearLayout.LayoutParams(0, -2, 1f))
                    row.addView(TextView(this).apply { text = "›"; textSize = 20f; setTextColor(accent) }, LinearLayout.LayoutParams(-2, -2))
                    discoveryList.addView(row, LinearLayout.LayoutParams(-1, -2))
                }
                if (!quiet) showStatus("Computer discovery finished.")
            }
        }
    }
    private fun api(target: ClientStore = store): GatewayApi {
        check(target.gateway.isNotBlank()) { "Choose a computer first." }
        return GatewayApi(target.gateway, target.token() ?: error("Pair this computer first."))
    }
    /** Every asynchronous UI result is fenced to the destination generation that started it. */
    private fun ui(ticket: Int, action: () -> Unit) { handler.post { if (!isDestroyed && epoch == ticket) action() } }
    private fun background(message: String? = null, work: (Int) -> Unit) {
        if (busy) { showStatus("A request is already in progress."); return }
        val ticket = epoch
        busy = true; message?.let { showStatus(it); setPhase(VoicePhase.THINKING) }
        executor.execute {
            try { work(ticket) } catch (failure: Exception) {
                val error = when (failure) {
                    is ApiFailure -> if (failure.status == 401) "This computer needs pairing again. Open the computer selector."
                        else if (failure.status in listOf(404, 409, 429) && failure.serverMessage.isNotBlank()) failure.serverMessage
                        else "The computer returned ${failure.code}. Your destination hasn't changed."
                    is java.io.IOException -> "Couldn't reach the computer. Check Tailscale and Chintu on that machine. Your destination hasn't changed."
                    is IllegalArgumentException, is IllegalStateException -> failure.message ?: "Couldn't load saved state."
                    else -> "Couldn't complete that request (${failure.javaClass.simpleName})."
                }
                ui(ticket) { showStatus(error); output.text = error; if (!speech.isSpeaking && !speech.isListening) setPhase(VoicePhase.IDLE) }
            } finally {
                handler.post { if (!isDestroyed) {
                    busy = false
                    if (!speech.isSpeaking && !speech.isListening) setPhase(VoicePhase.IDLE)
                    updateDestination(); scheduleListening()
                    if (foreground && ticket != epoch && !connected) {
                        setTranscript(store.draft); reviewPending = store.draft.isNotBlank()
                        selectedComputer?.let { if (store.isPaired()) connect(it, false) }
                    }
                } }
            }
        }
    }
    private fun setOptions(spinner: Spinner, values: List<String>) {
        spinner.adapter = object : ArrayAdapter<String>(this, android.R.layout.simple_spinner_dropdown_item, values) {
            override fun getView(position: Int, convertView: View?, parent: ViewGroup): View =
                (super.getView(position, convertView, parent) as TextView).apply {
                    setTextColor(if (position == 0) muted else ink); textSize = 15f; setPadding(dp(8), dp(12), dp(8), dp(12))
                }
            override fun getDropDownView(position: Int, convertView: View?, parent: ViewGroup): View =
                (super.getDropDownView(position, convertView, parent) as TextView).apply {
                    setTextColor(if (position == 0) muted else ink); textSize = 15f; setPadding(dp(16), dp(14), dp(16), dp(14))
                    setBackgroundColor(if (position == spinner.selectedItemPosition) Color.rgb(50, 36, 82) else Color.TRANSPARENT)
                }
        }
    }
    private fun updateProfiles() {
        if (!::profile.isInitialized) return
        val remembered = store.choice("profile")
        fun profiles(value: JSONObject?): Set<String> {
            val array = value?.optJSONArray("execution_profiles") ?: return emptySet()
            return (0 until array.length()).map { array.getString(it) }.toSet()
        }
        val choices = (profiles(projects.getOrNull(project.selectedItemPosition - 1)) intersect profiles(capabilities.getOrNull(provider.selectedItemPosition - 1))).sorted()
        setOptions(profile, listOf("Choose permission profile") + choices)
        // Default to the least capable profile: inspect is read-only inside the workspace. develop and desktop are
        // deliberate choices made in Settings, never implied.
        val fallback = listOf("inspect", "develop", "desktop").firstOrNull { it in choices }
        profile.setSelection(choices.indexOf(if (remembered in choices) remembered else fallback ?: "") + 1)
    }
    private fun persistChoices() {
        if (selectedComputer == null || !::profile.isInitialized) return
        val selectedProject = projects.getOrNull(project.selectedItemPosition - 1)?.getString("project_id").orEmpty()
        val selectedProvider = capabilities.getOrNull(provider.selectedItemPosition - 1)?.getString("provider").orEmpty()
        val selectedProfile = if (profile.selectedItemPosition > 0) profile.selectedItem.toString() else ""
        store.choose("project", selectedProject); store.choose("provider", selectedProvider); store.choose("profile", selectedProfile)
        runLabel.text = if (selectedProject.isEmpty() || selectedProvider.isEmpty() || selectedProfile.isEmpty()) "Choose workspace, agent & permissions in Settings"
            else "$selectedProject · $selectedProvider · $selectedProfile"
    }
    private fun loadTasks(client: GatewayApi): List<JSONObject> {
        val result = mutableListOf<JSONObject>(); var pageCursor: String? = null; val seen = mutableSetOf<String>()
        do {
            val suffix = pageCursor?.let { "&cursor=" + java.net.URLEncoder.encode(it, "UTF-8") }.orEmpty()
            val page = client.call("GET", "/api/v1/tasks?limit=10$suffix"); val array = page.getJSONArray("tasks")
            for (i in 0 until array.length()) result.add(array.getJSONObject(i))
            pageCursor = if (page.isNull("next_cursor")) null else page.getString("next_cursor")
            check(pageCursor == null || seen.add(pageCursor)) { "The computer repeated a task cursor." }
            check(result.size <= 10_000) { "Task history is too large to load." }
        } while (pageCursor != null)
        return result
    }
    private fun replaceTasks(values: List<JSONObject>) {
        val selected = selectedTask()?.optString("task_id") ?: guard.acceptedTaskId(); tasks = values
        setOptions(task, listOf("Choose a task") + values.map { "${it.getString("task_id").take(8)} · ${it.getString("provider")} · ${it.getString("state")}" })
        if (selected != null) { val index = tasks.indexOfFirst { it.getString("task_id") == selected }; if (index >= 0) task.setSelection(index + 1) }
    }
    private fun selectedTask(): JSONObject? = tasks.getOrNull(task.selectedItemPosition - 1)
    private fun echo() {
        val text = transcript.text.toString().trim(); val origin = store
        if (text.length !in 1..2000) { showStatus("Type or speak a short message first."); return }
        background("Testing the text connection…") { ticket ->
            val reply = api(origin).call("POST", "/api/v1/echo", JSONObject().put("text", text).toString()).getString("text")
            ui(ticket) { lastReply = reply; output.text = "Connection test (no agent): $reply"; showStatus("Your text reached ${selectedComputer?.name}."); showConversation() }
        }
    }
    // --- Event stream: pushes each event as it commits; polling remains the source of truth for the cursor. ---
    private fun startStream() {
        stopStream()
        if (!foreground || !connected || !store.isPaired()) return
        val ticket = epoch; val origin = store
        val token = try { origin.token() } catch (_: Exception) { null } ?: return
        stream = EventStream(origin.gateway, token, cursor.get(),
            { event -> handler.post { if (epoch == ticket && store === origin) onStreamEvent(event) } },
            { state -> handler.post { if (epoch == ticket && store === origin) onStreamState(state) } }).also { it.start() }
    }
    private fun stopStream() { stream?.stop(); stream = null; streamLive = false }
    private fun onStreamState(state: EventStream.State) {
        val wasLive = streamLive
        streamLive = state == EventStream.State.LIVE
        android.util.Log.i("Chintu", "event stream: $state")
        // Either way the poll path catches up: on connect it covers the gap before the stream, on loss it resumes its cadence.
        if (streamLive != wasLive) requestPoll()
        // The stream came back after polling gave up: the computer is reachable again, so reconnect fully.
        if (streamLive && !polling && !busy) selectedComputer?.let { connect(it, false) }
    }
    private fun onStreamEvent(event: JSONObject) {
        // Events still arrive although a poll failed: the computer is reachable, so restore the full connection.
        if (!polling) { if (!busy) selectedComputer?.let { connect(it, false) }; return }
        val kind = event.getString("kind"); val taskId = event.getString("task_id")
        if (kind in setOf("progress", "output", "output_delta") && chatTurns.any { it.getString("task_id") == taskId }) {
            chatEvents.getOrPut(taskId) { ChatTranscript() }.add(event.getLong("event_id"), kind, event.getJSONObject("payload").optString("text"))
            if (chatOpen) { handler.removeCallbacks(renderSoon); handler.postDelayed(renderSoon, 120) }
        }
        // Deltas only feed the chat; anything else takes the normal poll path (speech, activity, snapshots, cursor).
        if (kind != "output_delta") requestPoll()
    }
    /** A prompt poll, at most one about every 0.7 s however fast events arrive. */
    private fun requestPoll() {
        if (!foreground || !polling) return
        handler.removeCallbacks(poll)
        handler.postDelayed(poll, maxOf(150L, 700L - (System.currentTimeMillis() - lastPollAt)))
    }
    // --- Conversations: voice and chat turns share one agent session and working directory. ---
    private fun JSONObject.text(key: String): String = if (isNull(key)) "" else optString(key)
    private fun enc(value: String): String = java.net.URLEncoder.encode(value, "UTF-8")
    private fun shortTitle(text: String): String {
        val line = text.trim().split(Regex("\\s+")).joinToString(" ")
        return if (line.length <= 60) line else line.take(59).trimEnd() + "…"
    }
    private fun stamp(value: String): Long = try { java.time.Instant.parse(value).toEpochMilli() } catch (_: Exception) { 0L }
    private fun folderLabel(path: String) = "📁 " + path.ifEmpty { "workspace root" }
    private fun folderSpoken(path: String) = if (path.isEmpty()) "the workspace" else "the ${path.substringAfterLast('/')} folder"
    /** Workspace, agent and permission profile chosen in Settings; a conversation pins all three. Main thread only. */
    private fun selection(): Triple<String, String, String>? {
        if (!::profile.isInitialized || profile.selectedItemPosition <= 0) return null
        val chosenProject = projects.getOrNull(project.selectedItemPosition - 1)?.getString("project_id") ?: return null
        val chosenProvider = capabilities.getOrNull(provider.selectedItemPosition - 1)?.getString("provider") ?: return null
        return Triple(chosenProject, chosenProvider, profile.selectedItem.toString())
    }
    private fun sameChoice(chat: JSONObject, choice: Triple<String, String, String>) = chat.optString("project_id") == choice.first &&
        chat.optString("provider") == choice.second && chat.optString("execution_profile") == choice.third
    private fun freshChat(choice: Triple<String, String, String>, folder: String) = JSONObject()
        .put("conversation_id", java.util.UUID.randomUUID().toString()).put("project_id", choice.first).put("provider", choice.second)
        .put("execution_profile", choice.third).put("working_directory", folder).put("title", "").put("created", false)
    private fun chatFromServer(value: JSONObject) = JSONObject().put("conversation_id", value.getString("conversation_id"))
        .put("project_id", value.getString("project_id")).put("provider", value.getString("provider"))
        .put("execution_profile", value.getString("execution_profile")).put("working_directory", value.text("working_directory"))
        .put("title", value.text("title")).put("created", true)
    /**
     * The conversation the next turn joins, created on the computer first (idempotently, by its client ID).
     * Changing workspace, agent or profile in Settings starts a new one in the same folder when the workspace is unchanged.
     * Runs off the main thread.
     */
    private fun ensureConversation(origin: ClientStore, choice: Triple<String, String, String>): JSONObject {
        var chat = origin.conversation
        if (chat == null || !sameChoice(chat, choice)) {
            val keep = chat?.takeIf { it.optString("project_id") == choice.first }?.text("working_directory").orEmpty()
            chat = freshChat(choice, keep); origin.conversation = chat
        }
        if (chat.optBoolean("created")) return chat
        val body = JSONObject().put("conversation_id", chat.getString("conversation_id")).put("project_id", choice.first)
            .put("provider", choice.second).put("execution_profile", choice.third).put("working_directory", chat.text("working_directory"))
        val response = try { api(origin).call("POST", "/api/v1/conversations", body.toString()) } catch (e: ApiFailure) {
            // A vanished folder or a clash cannot be retried as is; the next message starts again at the workspace root.
            if (e.status in listOf(404, 409, 422)) origin.conversation = null
            throw e
        }
        val created = chatFromServer(response.getJSONObject("conversation"))
        origin.conversation = created
        return created
    }
    private fun updateChatHeader() {
        if (!::chatLabel.isInitialized) return
        val chat = if (store.gateway.isBlank()) null else store.conversation
        val title = chat?.text("title")?.ifEmpty { null } ?: "New chat"
        chatLabel.text = "💬  $title  ›"
        chatTitle.text = title
        val detail = chat?.let { "${folderLabel(it.text("working_directory"))} · ${it.optString("execution_profile")}" } ?: "Starts with your next message"
        chatSubtitle.text = listOfNotNull(selectedComputer?.name, detail).joinToString("  ·  ")
    }
    private fun bubble(text: String, mine: Boolean, note: String?, alert: Boolean = false, pending: Boolean = false, extra: View? = null): TextView {
        val row = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; gravity = if (mine) Gravity.END else Gravity.START }
        val body = TextView(this).apply {
            this.text = text; textSize = 15f; setTextColor(if (pending) muted else ink); setLineSpacing(dp(3).toFloat(), 1f)
            maxWidth = (resources.displayMetrics.widthPixels * .78f).toInt(); setPadding(dp(14), dp(10), dp(14), dp(10))
            background = shape(if (mine) Color.rgb(72, 48, 109) else surface, 18, if (alert) Color.rgb(255, 150, 140) else null)
            isClickable = true; isFocusable = true
        }
        row.addView(body, LinearLayout.LayoutParams(-2, -2))
        note?.takeIf { it.isNotBlank() }?.let { row.addView(TextView(this).apply { this.text = it; textSize = 11f; setTextColor(muted); setPadding(dp(6), dp(2), dp(6), 0) }) }
        extra?.let { row.addView(it) }
        chatMessages.addView(row, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })
        return body
    }
    private fun copy(text: String) {
        getSystemService(android.content.ClipboardManager::class.java)?.setPrimaryClip(android.content.ClipData.newPlainText("Chintu", text))
        showStatus("Copied.")
    }
    private val activeStates = setOf("queued", "running", "waiting_for_input", "cancelling")
    /** One quiet line per tool step, so the whole run is visible without crowding the replies. */
    private fun stepLine(text: String) {
        chatMessages.addView(TextView(this).apply {
            this.text = "▸  $text"; textSize = 12f; setTextColor(muted); maxLines = 3; typeface = Typeface.MONOSPACE
            ellipsize = android.text.TextUtils.TruncateAt.END; setPadding(dp(10), dp(4), dp(10), 0)
        }, LinearLayout.LayoutParams(-1, -2))
    }
    /**
     * WhatsApp-style thread: your words on the right; on the left everything the agent does, streamed as it happens
     * (tool steps as quiet lines, reply text growing in its bubble). Tap a reply to hear it, hold it to copy.
     */
    private fun renderChat() {
        if (!::chatMessages.isInitialized) return
        updateChatHeader()
        // Follow new output only while the owner is at the bottom; never pull them away from what they are reading.
        val follow = chatMessages.height == 0 || chatScroll.scrollY + chatScroll.height >= chatMessages.height - dp(96)
        chatMessages.removeAllViews()
        if (chatTurns.isEmpty()) {
            label(chatMessages, "Type below or tap the mic. Whatever you say by voice joins this chat too, and the agent remembers " +
                "everything in it until you start a new chat.", 14f, muted).apply { gravity = Gravity.CENTER; setPadding(dp(12), dp(40), dp(12), dp(12)) }
            return
        }
        chatTurns.forEach { turn ->
            val id = turn.getString("task_id"); val state = turn.getString("state"); val result = turn.optJSONObject("result")
            bubble(turn.optString("instruction"), mine = true, note = timeAgo(stamp(turn.optString("created_at"))))
            val active = state in activeStates
            val segments = chatEvents[id]?.segments().orEmpty()
            var texts = 0
            segments.forEach { segment ->
                if (segment.kind() == ChatTranscript.Kind.STEP) stepLine(segment.text())
                else {
                    texts++
                    val view = bubble(segment.text() + if (segment.open()) " ▍" else "", mine = false, note = null)
                    view.setOnClickListener { speak(segment.text()) }
                    view.setOnLongClickListener { copy(segment.text()); true }
                }
            }
            val summary = result?.optString("summary").orEmpty()
            // The recorded result is its own reply when nothing streamed, or when the run did not succeed.
            if (!active && summary.isNotBlank() && (texts == 0 || state != "succeeded")) {
                val view = bubble(summary, mine = false, note = null, alert = state in setOf("failed", "interrupted"))
                val spoken = result?.optString("speech_text").orEmpty().ifBlank { summary }
                view.setOnClickListener { speak(spoken) }
                view.setOnLongClickListener { copy(summary); true }
            } else if (active && segments.isEmpty()) {
                bubble(when (state) { "queued" -> "Queued, waiting its turn…"; "cancelling" -> "Stopping…"; else -> "Working on it…" },
                    mine = false, note = null, pending = true)
            }
            val footer = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
            footer.addView(TextView(this).apply {
                text = if (active) (if (state == "cancelling") "stopping…" else "● live") else
                    timeAgo(stamp(turn.optString("updated_at"))) + if (state == "succeeded") "" else " · $state"
                textSize = 11f; setTextColor(if (active) accent else muted); setPadding(dp(6), dp(2), dp(6), 0)
            })
            fun link(label: String, description: String, action: () -> Unit) = footer.addView(TextView(this).apply {
                text = label; textSize = 13f; setTextColor(accent); setPadding(dp(10), dp(6), dp(10), dp(6)); minHeight = dp(36)
                gravity = Gravity.CENTER_VERTICAL; contentDescription = description; setOnClickListener { action() }
            })
            if (active && state != "cancelling") link("Stop", "Stop this request on the computer") { cancelTurn(id) }
            if (!active && segments.isEmpty() && id !in chatExpanded) link("Show full output", "Show every step and the full reply") { loadTurnEvents(id) }
            chatMessages.addView(footer, LinearLayout.LayoutParams(-1, -2))
        }
        if (follow) chatScroll.post { chatScroll.fullScroll(View.FOCUS_DOWN) }
    }
    /** A turn's text events (steps, streamed and finished replies), oldest first and bounded. Off the main thread. */
    private fun fetchTurnEvents(client: GatewayApi, id: String): List<JSONObject> {
        val found = mutableListOf<JSONObject>(); var after = 0L
        do {
            val page = client.call("GET", "/api/v1/events?task_id=$id&after=$after&limit=100"); val array = page.getJSONArray("events")
            for (i in 0 until array.length()) found.add(array.getJSONObject(i))
            val next = page.getLong("next_cursor")
            val more = page.getBoolean("has_more") && next > after
            after = next
        } while (more && found.size < 3000)
        return found
    }
    private fun addTurnEvents(id: String, events: List<JSONObject>) {
        val transcript = chatEvents.getOrPut(id) { ChatTranscript() }
        events.forEach { transcript.add(it.getLong("event_id"), it.getString("kind"), it.getJSONObject("payload").optString("text")) }
    }
    private fun loadTurnEvents(id: String) {
        val origin = store
        chatExpanded.add(id)
        background("Loading the full output…") { ticket ->
            val events = fetchTurnEvents(api(origin), id)
            ui(ticket) { addTurnEvents(id, events); renderChat() }
        }
    }
    /** Reloads the current conversation's turns from the computer (oldest first, newest 200 kept). */
    private fun loadChat() {
        val chat = store.conversation; val origin = store
        if (chat == null || !chat.optBoolean("created") || !connected || !store.isPaired()) { if (chat == null) chatTurns.clear(); renderChat(); return }
        if (busy) { handler.postDelayed({ if (chatOpen) loadChat() }, 800); return }
        val id = chat.getString("conversation_id")
        background { ticket ->
            val client = api(origin)
            val fresh = try { client.call("GET", "/api/v1/conversations/$id") } catch (e: ApiFailure) {
                if (e.status != 404) throw e
                origin.conversation = null
                ui(ticket) { chatTurns.clear(); renderChat(); showStatus("That chat is no longer on the computer. Your next message starts a new one.") }
                return@background
            }
            val turns = mutableListOf<JSONObject>(); var next: String? = null; val seen = mutableSetOf<String>()
            do {
                val suffix = next?.let { "&cursor=" + enc(it) }.orEmpty()
                val page = client.call("GET", "/api/v1/conversations/$id/tasks?limit=100$suffix"); val array = page.getJSONArray("tasks")
                for (i in 0 until array.length()) turns.add(array.getJSONObject(i))
                val cursorValue = if (page.isNull("next_cursor")) null else page.getString("next_cursor")
                check(cursorValue == null || seen.add(cursorValue)) { "The computer repeated a chat cursor." }
                next = cursorValue
            } while (next != null && turns.size < 1000)
            val kept = turns.takeLast(200)
            val backfill = (kept.filter { it.getString("state") in activeStates } + listOfNotNull(kept.lastOrNull()))
                .map { it.getString("task_id") }.distinct().takeLast(5).associateWith { fetchTurnEvents(client, it) }
            ui(ticket) {
                if (origin.conversation?.optString("conversation_id") == id) {
                    origin.conversation = chatFromServer(fresh); chatTurns = kept.toMutableList()
                    backfill.forEach { (task, events) -> addTurnEvents(task, events); chatExpanded.add(task) }
                    renderChat()
                }
            }
        }
    }
    private fun cancelTurn(id: String) {
        val origin = store
        background("Asking the computer to stop…") { ticket ->
            val body = JSONObject().put("message_id", java.util.UUID.randomUUID().toString()).toString()
            val task = api(origin).call("POST", "/api/v1/tasks/$id/cancel", body).getJSONObject("task")
            ui(ticket) {
                val index = chatTurns.indexOfFirst { it.getString("task_id") == id }
                if (index >= 0) chatTurns[index] = task
                renderChat(); showStatus("Asked ${selectedComputer?.name ?: "the computer"} to stop that request.")
            }
        }
    }
    /** A sheet over the chat (chats list, new chat, tools); voice pauses while it is open. */
    private fun openOverlay(title: String, body: View): android.app.Dialog {
        overlay?.dismiss()
        inDialog = true; waitingForRoute = false; handler.removeCallbacks(routeTimeout); handler.removeCallbacks(listenAgain)
        if (::speech.isInitialized) speech.cancelRecognition()
        lateinit var dialog: android.app.Dialog
        dialog = bottomSheet(title, body, tall = true) {
            if (overlay === dialog) overlay = null
            inDialog = (sheet?.isShowing == true && !chatOpen) || audioPicker?.isShowing == true; scheduleListening()
        }
        overlay = dialog
        showSheet(dialog, tall = true)
        return dialog
    }
    private fun showChats() {
        if (!connected || !store.isPaired()) { showStatus("Connect a computer to see its chats."); return }
        val origin = store
        background("Loading chats…") { ticket ->
            val page = api(origin).call("GET", "/api/v1/conversations?limit=50")
            val array = page.getJSONArray("conversations")
            val rows = (0 until array.length()).map { array.getJSONObject(it) }
            ui(ticket) {
                val scroller = ScrollView(this); val body = column(scroller, 20)
                lateinit var dialog: android.app.Dialog
                button(body, "＋ New chat", true) { dialog.dismiss(); showNewChat() }; gap(body)
                if (rows.isEmpty()) label(body, "No chats yet on ${selectedComputer?.name ?: "this computer"}. Your first message starts one.", 14f, muted)
                val current = origin.conversation?.optString("conversation_id")
                rows.forEach { value ->
                    val mine = value.getString("conversation_id") == current
                    val row = column(padding = 14).apply { background = shape(surface, 18, if (mine) accent else null); isClickable = true; isFocusable = true }
                    val head = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
                    row.addView(head, LinearLayout.LayoutParams(-1, -2))
                    if (value.getString("state") == "working") head.addView(TextView(this).apply { text = "◌"; textSize = 13f; setTextColor(accent) }, LinearLayout.LayoutParams(dp(20), -2))
                    head.addView(TextView(this).apply {
                        text = value.text("title").ifEmpty { "New chat" }; textSize = 15f; setTextColor(ink); maxLines = 1
                        ellipsize = android.text.TextUtils.TruncateAt.END; typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
                    }, LinearLayout.LayoutParams(0, -2, 1f))
                    head.addView(TextView(this).apply { text = timeAgo(stamp(value.optString("updated_at"))); textSize = 12f; setTextColor(muted) }, LinearLayout.LayoutParams(-2, -2))
                    label(row, "${folderLabel(value.text("working_directory"))} · ${value.optString("execution_profile")} · ${value.optInt("turns")} messages", 12f, muted)
                    value.text("last_text").takeIf { it.isNotEmpty() }?.let { label(row, it, 13f, ink).apply { maxLines = 2; ellipsize = android.text.TextUtils.TruncateAt.END } }
                    row.contentDescription = value.text("title").ifEmpty { "New chat" } + if (mine) ", current chat" else ""
                    row.setOnClickListener { dialog.dismiss(); selectChat(value) }
                    row.setOnLongClickListener { confirmArchive(value, dialog); true }
                    body.addView(row, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(8) })
                }
                label(body, if (page.isNull("next_cursor")) "Hold a chat to archive it." else "Showing the 50 most recent chats. Hold a chat to archive it.", 12f, muted)
                dialog = openOverlay("Chats · ${selectedComputer?.name.orEmpty()}", scroller)
            }
        }
    }
    private fun selectChat(value: JSONObject) {
        store.conversation = chatFromServer(value); chatTurns.clear(); chatEvents.clear(); chatExpanded.clear()
        applyChoice(value)
        if (chatOpen && sheet?.isShowing == true) { renderChat(); loadChat() } else showConversation()
    }
    /** Opening an older chat also selects its workspace, agent and profile, so the next message continues it. */
    private fun applyChoice(chat: JSONObject) {
        val projectIndex = projects.indexOfFirst { it.getString("project_id") == chat.getString("project_id") }
        val providerIndex = capabilities.indexOfFirst { it.getString("provider") == chat.getString("provider") }
        if (projectIndex < 0 || providerIndex < 0) {
            showStatus("That chat's workspace or agent isn't offered any more, so your next message starts a new chat."); return
        }
        rebuildingSelections = true
        project.setSelection(projectIndex + 1); provider.setSelection(providerIndex + 1)
        store.choose("profile", chat.getString("execution_profile")); updateProfiles()
        rebuildingSelections = false; persistChoices()
        if (selection()?.third != chat.getString("execution_profile"))
            showStatus("That chat's permission profile isn't offered any more, so your next message starts a new chat.")
    }
    private fun confirmArchive(value: JSONObject, list: android.app.Dialog) {
        val body = column(padding = 20)
        label(body, "Hide “${value.text("title").ifEmpty { "this chat" }}” from your chats? The computer keeps its history.", 15f); gap(body)
        val dialog = bottomSheet("Archive chat", body)
        button(body, "Archive", true) {
            dialog.dismiss(); list.dismiss()
            val origin = store; val id = value.getString("conversation_id")
            background("Archiving…") { ticket ->
                api(origin).call("DELETE", "/api/v1/conversations/$id")
                ui(ticket) {
                    if (origin.conversation?.optString("conversation_id") == id) { origin.conversation = null; chatTurns.clear(); renderChat() }
                    showStatus("Chat archived.")
                }
            }
        }
        gap(body, 8)
        button(body, "Keep it") { dialog.dismiss() }
        showSheet(dialog)
    }
    /** New chat: a fresh agent session in a folder chosen from the workspace. */
    private fun showNewChat() {
        val choice = selection()
        if (!connected || choice == null) {
            if (!connected) showStatus("Connect a computer first.") else { showStatus("Choose a workspace, agent and permission profile first."); showPage(2) }
            return
        }
        val workspaceName = projects.firstOrNull { it.getString("project_id") == choice.first }?.optString("display_name") ?: choice.first
        val scroller = ScrollView(this); val body = column(scroller, 20)
        label(body, "A new chat starts a fresh agent session. Pick the folder it works in; the chat keeps that folder.", 13f, muted); gap(body)
        label(body, "$workspaceName  ·  ${choice.third}", 15f, ink, true)
        label(body, "Change the workspace or permission profile in Settings.", 12f, muted); gap(body)
        val where = label(body, folderLabel(""), 16f, accent, true); gap(body, 8)
        var current = store.conversation?.takeIf { it.optString("project_id") == choice.first }?.text("working_directory").orEmpty()
        lateinit var dialog: android.app.Dialog
        button(body, "Start chat in this folder", true) { dialog.dismiss(); startChat(choice, current, announce = false) }; gap(body)
        val list = column(body)
        val origin = store
        fun browse(path: String) {
            background { ticket ->
                val page = try {
                    api(origin).call("GET", "/api/v1/directories?project_id=${enc(choice.first)}&path=${enc(path)}")
                } catch (e: ApiFailure) {
                    if (e.status != 404 || path.isEmpty()) throw e
                    api(origin).call("GET", "/api/v1/directories?project_id=${enc(choice.first)}&path=")
                }
                val entries = page.getJSONArray("entries")
                ui(ticket) {
                    current = page.text("path"); where.text = folderLabel(current); list.removeAllViews()
                    if (!page.isNull("parent")) {
                        val up = page.text("parent")
                        button(list, "↑  Up to ${up.ifEmpty { "workspace root" }}") { handler.post { browse(up) } }; gap(list, 6)
                    }
                    if (entries.length() == 0) label(list, "No folders inside this one.", 13f, muted)
                    for (i in 0 until entries.length()) {
                        val entry = entries.getJSONObject(i)
                        button(list, "📁  ${entry.getString("name")}  ›") { handler.post { browse(entry.getString("path")) } }; gap(list, 6)
                    }
                    if (page.optBoolean("truncated")) label(list, "Only the first 500 folders are shown.", 12f, muted)
                }
            }
        }
        dialog = openOverlay("New chat", scroller)
        browse(current)
    }
    private fun startChat(choice: Triple<String, String, String>, folder: String, announce: Boolean) {
        val origin = store
        origin.conversation = freshChat(choice, folder); chatTurns.clear(); chatEvents.clear(); chatExpanded.clear(); renderChat()
        background("Starting a new chat…") { ticket ->
            val chat = ensureConversation(origin, choice)
            ui(ticket) {
                renderChat()
                val message = "New chat in ${folderSpoken(chat.text("working_directory"))}. I'm starting fresh."
                if (announce) respond(message) else { showStatus(message); handler.post { showConversation() } }
            }
        }
    }
    /** "Chintu, new chat [in FOLDER]": the folder is matched by its exact name at the top of the workspace or in the current folder. */
    private fun startChatByVoice(folder: String?) {
        val choice = selection()
        if (selectedComputer == null || !connected || choice == null) {
            respond(if (!connected) "Connect a computer first, then ask me for a new chat." else "Choose a workspace and permission profile in Settings first.")
            return
        }
        val keep = store.conversation?.takeIf { it.optString("project_id") == choice.first }?.text("working_directory").orEmpty()
        if (folder == null) { startChat(choice, keep, announce = true); return }
        val origin = store
        background("Looking for the $folder folder…") { ticket ->
            val candidates = linkedMapOf<String, String>()
            for (base in listOf("", keep).distinct()) {
                val page = try {
                    api(origin).call("GET", "/api/v1/directories?project_id=${enc(choice.first)}&path=${enc(base)}")
                } catch (e: ApiFailure) { if (e.status == 404) continue else throw e }
                if (base.isNotEmpty()) candidates.putIfAbsent(base, base.substringAfterLast('/'))
                val entries = page.getJSONArray("entries")
                for (i in 0 until entries.length()) entries.getJSONObject(i).let { candidates.putIfAbsent(it.getString("path"), it.getString("name")) }
            }
            val match = DestinationMatcher.match(folder, candidates.filter { VoiceCommand.normalize(it.value).isNotEmpty() }
                .map { (path, name) -> DestinationMatcher.Destination(path, name, "") })
            ui(ticket) {
                when (match.status()) {
                    DestinationMatcher.Status.MATCHED -> handler.post { startChat(choice, match.destination().id(), announce = true) }
                    DestinationMatcher.Status.AMBIGUOUS -> respond("More than one folder is called $folder. Pick the one you mean on screen.")
                    else -> respond("I couldn't find a folder called $folder. Say Chintu, new chat, or pick a folder on screen.")
                }
            }
        }
    }
    /** What the agent can use in the current chat: built-in tools, MCP servers with their tools, and skills. */
    private fun showTools() {
        val chat = store.conversation; val choice = selection()
        val projectId = chat?.optString("project_id")?.ifEmpty { null } ?: choice?.first
        val profileName = chat?.optString("execution_profile")?.ifEmpty { null } ?: choice?.third
        if (!connected || projectId == null || profileName == null) { showStatus("Connect a computer and choose a workspace first."); return }
        val folder = chat?.text("working_directory").orEmpty(); val origin = store; val name = selectedComputer?.name ?: "the computer"
        background("Asking $name which tools it has…") { ticket ->
            val catalog = api(origin).call("GET", "/api/v1/tools?project_id=${enc(projectId)}&execution_profile=${enc(profileName)}&working_directory=${enc(folder)}")
            ui(ticket) { renderTools(catalog, name) }
        }
    }
    private fun renderTools(catalog: JSONObject, computer: String) {
        val scroller = ScrollView(this); val body = column(scroller, 20)
        val profileName = catalog.optString("execution_profile")
        label(body, when (catalog.optString("source")) {
            "probe" -> "Live from Claude Code on $computer."
            "cached" -> "From the last check on $computer."
            else -> "Fixed for the $profileName profile."
        }, 13f, muted)
        catalog.text("reason").takeIf { it.isNotEmpty() }?.let { gap(body, 6); label(body, it, 13f, muted) }
        fun list(key: String) = catalog.optJSONArray(key)?.let { array -> (0 until array.length()).map { array.getJSONObject(it) } }.orEmpty()
        val tools = list("tools"); val servers = list("mcp_servers"); val skills = list("skills")
        fun section(title: String) { gap(body, 14); label(body, title, 17f, ink, true); gap(body, 4) }
        fun entry(parent: LinearLayout, name: String, detail: String) {
            label(parent, name, 14f, ink, true).setPadding(0, dp(6), 0, 0)
            if (detail.isNotEmpty()) label(parent, detail, 12f, muted)
        }
        val builtins = tools.filter { it.optString("kind") == "builtin" }
        if (builtins.isNotEmpty()) { section("Built-in tools · ${builtins.size}"); builtins.forEach { entry(body, it.getString("name"), it.text("description")) } }
        if (servers.isNotEmpty()) {
            section("MCP servers · ${servers.size}")
            servers.forEach { server ->
                val box = card(body)
                val status = server.optString("status")
                val color = when (status) { "connected" -> Color.rgb(140, 230, 170); "failed" -> Color.rgb(255, 150, 140); else -> muted }
                label(box, "●  ${server.getString("name")}", 15f, ink, true).setTextColor(color)
                label(box, "$status · ${server.optInt("tools")} tools", 12f, muted)
                tools.filter { it.optString("kind") == "mcp" && it.text("server") == server.getString("name") }
                    .forEach { entry(box, it.getString("name").substringAfterLast("__"), it.text("description")) }
            }
        }
        if (skills.isNotEmpty()) { section("Skills & commands · ${skills.size}"); skills.forEach { entry(body, "/" + it.getString("name"), it.text("description")) } }
        if (tools.isEmpty() && servers.isEmpty() && skills.isEmpty()) { gap(body); label(body, "No tools reported for this chat.", 14f, muted) }
        openOverlay("Tools · $profileName", scroller)
    }
    private fun submit() {
        if (busy) { showStatus("Wait for the current request to finish."); reviewPending = true; return }
        val origin = store; val submission = guard; val destination = selectedComputer?.name ?: "computer"
        val registered = projects.getOrNull(project.selectedItemPosition - 1); val enabled = capabilities.getOrNull(provider.selectedItemPosition - 1)
        val selectedProfile = if (profile.selectedItemPosition > 0) profile.selectedItem.toString() else null
        val text = transcript.text.toString().trim()
        if (submission.pending() == null && (!connected || registered == null || enabled == null || selectedProfile == null || text.length !in 1..8000)) {
            reviewPending = true
            val reason = when {
                !connected -> "I'm not connected to ${destination} right now. Say Chintu, connect me to ${destination}."
                registered == null -> "Choose a workspace in Settings first."
                enabled == null -> "Choose an agent in Settings first."
                selectedProfile == null -> "Choose a permission profile in Settings first."
                else -> "That instruction is too long to send."
            }
            respond(reason); return
        }
        // A reserved Chintu command can never be submitted via the task button or automatic sending.
        if (submission.pending() == null && VoiceCommand.parse(text).kind() != VoiceCommand.Kind.TASK) { onUtterance(text); return }
        speech.cancelRecognition(); handler.removeCallbacks(listenAgain); origin.draft = text
        val choice = selection()
        background("Sending to $destination…") { ticket ->
            // Every instruction is a turn of the current conversation, so the agent resumes the same session.
            val request = submission.prepare { key ->
                val chat = ensureConversation(origin, choice ?: error("Choose a workspace, agent and permission profile in Settings first."))
                JSONObject().put("submission_key", key)
                    .put("project_id", registered!!.getString("project_id")).put("provider", enabled!!.getString("provider"))
                    .put("execution_profile", selectedProfile).put("instruction", text)
                    .put("conversation_id", chat.getString("conversation_id")).toString() }
            val response = try { api(origin).call("POST", "/api/v1/tasks", request) } catch (e: ApiFailure) {
                if (e.status in listOf(400, 413, 422, 501)) submission.rejected()
                if (e.status == 404 || (e.status == 409 && "start a new one" in e.serverMessage)) {
                    // The chat is gone, archived or no longer matches: the next send starts a new one.
                    submission.rejected(); origin.conversation = null
                    error(e.serverMessage.ifBlank { "That chat can't take new messages." } + " Send it again to start a new chat.")
                }
                throw e
            }
            val accepted = response.getJSONObject("task"); validateAccepted(accepted, JSONObject(request))
            check(response.get("replayed") is Boolean) { "The receipt is incomplete. Retry the saved instruction." }
            submission.accepted(accepted.getString("task_id"))
            ui(ticket) {
                replaceTasks(listOf(accepted) + tasks.filter { it.getString("task_id") != accepted.getString("task_id") })
                task.setSelection(1)
                if (transcript.text.toString().trim() == JSONObject(request).getString("instruction")) setTranscript("")
                origin.draft = transcript.text.toString(); reviewPending = origin.draft.isNotBlank(); showTask(accepted)
                if (!reviewPending && !chatOpen) dismissSheet()
                activityStore().record(accepted); updateActivityBadge()
                origin.conversation?.let { chat -> if (chat.text("title").isEmpty()) { chat.put("title", shortTitle(text)); origin.conversation = chat } }
                if (chatTurns.any { it.text("conversation_id") != accepted.text("conversation_id") }) { chatTurns.clear(); chatEvents.clear(); chatExpanded.clear() }
                chatTurns.removeAll { it.getString("task_id") == accepted.getString("task_id") }; chatTurns.add(accepted); renderChat()
                showStatus(if (quiet.isChecked) "Queued on $destination. The result will appear in Activity."
                    else "Instruction accepted by $destination${if (response.getBoolean("replayed")) " (existing task)" else ""}.")
                working = true; workingInstruction = text; setPhase(VoicePhase.THINKING)
                lastUpdateSpokenAt = System.currentTimeMillis(); lastProgressAt = lastUpdateSpokenAt; lastSpokenUpdate = ""
                if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED &&
                    !preferences.getBoolean("notifications_asked", false)) {
                    preferences.edit().putBoolean("notifications_asked", true).apply()
                    requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 4)
                }
                polling = true; handler.removeCallbacks(poll); if (foreground) handler.post(poll)
            }
        }
    }
    private fun validateAccepted(value: JSONObject, submitted: JSONObject) {
        val id = value.getString("task_id")
        check(java.util.UUID.fromString(id).toString().equals(id, ignoreCase = true)) { "Invalid task receipt; saved instruction retained." }
        for (field in listOf("provider", "project_id", "execution_profile")) check(value.getString(field) == submitted.getString(field)) { "The receipt doesn't match; saved instruction retained." }
        check(value.text("conversation_id") == submitted.text("conversation_id")) { "The receipt names another chat; saved instruction retained." }
        check(value.getString("state") in setOf("queued", "running", "waiting_for_input", "cancelling", "succeeded", "failed", "cancelled", "interrupted"))
        val version = value.get("task_version")
        check((version is Int || version is Long) && (version as Number).toLong() >= 1)
        check(value.getString("instruction").isNotBlank() && value.has("result") && value.has("pending_input"))
    }
    private fun readTask() {
        val id = selectedTask()?.getString("task_id") ?: run { showStatus("Choose a task first."); return }
        val origin = store
        background("Reading the task…") { ticket ->
            val value = api(origin).call("GET", "/api/v1/tasks/$id")
            ui(ticket) { if (selectedTask()?.optString("task_id") == id) { showTask(value); showConversation() } }
        }
    }
    private fun showTask(value: JSONObject) {
        val result = value.optJSONObject("result"); lastReply = result?.optString("speech_text").orEmpty()
        output.text = "${value.getString("provider")} · ${value.getString("state")}\n" +
            (result?.optString("summary") ?: value.optJSONObject("pending_input")?.optString("question") ?: "Your computer is working on it.")
    }
    private fun pollEvents() {
        if (busy) { if (foreground && polling) { handler.removeCallbacks(poll); handler.postDelayed(poll, 1000) }; return }
        lastPollAt = System.currentTimeMillis()
        val origin = store; val eventCursor = cursor; val selectedId = selectedTask()?.getString("task_id"); val speakNew = speakingAllowed()
        val watched = chatTurns.map { it.getString("task_id") }.toSet()
        background { ticket ->
            try {
                val client = api(origin)
                val page = client.call("GET", "/api/v1/events?after=${eventCursor.get()}&limit=100")
                val events = page.getJSONArray("events"); val messages = mutableListOf<String>(); val live = eventCursor.canSpeak(); var changed = false
                val progressLines = mutableListOf<Pair<String, String>>(); var finished = false
                val finishedIds = mutableSetOf<String>()
                val chatChanged = linkedSetOf<String>(); val chatLines = mutableListOf<JSONObject>()
                for (i in 0 until events.length()) {
                    val event = events.getJSONObject(i)
                    if (!eventCursor.accept(event.getLong("event_id"))) continue
                    if (event.getString("task_id") in watched) {
                        // Text streams straight into the chat; only state changes need a fresh snapshot.
                        if (event.getString("kind") in listOf("progress", "output", "output_delta")) chatLines.add(event)
                        else chatChanged.add(event.getString("task_id"))
                    }
                    if (event.getString("kind") in listOf("completed", "failed", "cancelled", "interrupted")) finishedIds.add(event.getString("task_id"))
                    if (event.getString("task_id") == selectedId) {
                        changed = true
                        val kind = event.getString("kind")
                        if (kind in listOf("progress", "output")) progressLines.add(kind to event.getJSONObject("payload").optString("text"))
                        if (kind in listOf("completed", "failed", "cancelled", "interrupted")) {
                            finished = true
                            if (live && speakNew) { val text = event.getJSONObject("payload").optString("speech_text"); if (text.isNotBlank()) messages.add(text) }
                        }
                    }
                }
                eventCursor.finishPage(page.getLong("next_cursor"), page.getBoolean("has_more")); origin.cursor = eventCursor.get()
                val snapshot = if (changed && selectedId != null) client.call("GET", "/api/v1/tasks/$selectedId") else null
                val chatSnapshots = chatChanged.filter { it != selectedId }.takeLast(5).map { client.call("GET", "/api/v1/tasks/$it") } +
                    listOfNotNull(snapshot?.takeIf { selectedId != null && selectedId in chatChanged })
                // Final outcomes of every task land in the on-phone activity log, whichever task is selected.
                val log = ActivityStore(this, origin.gateway)
                finishedIds.forEach { id -> try { log.record(client.call("GET", "/api/v1/tasks/$id")) } catch (_: Exception) { } }
                ui(ticket) {
                    if (finishedIds.isNotEmpty()) { updateActivityBadge(); if (sheet?.isShowing == true && activityList.parent != null) renderActivity() }
                    if (chatSnapshots.isNotEmpty() || chatLines.isNotEmpty()) {
                        chatLines.forEach { event ->
                            chatEvents.getOrPut(event.getString("task_id")) { ChatTranscript() }.add(event.getLong("event_id"),
                                event.getString("kind"), event.getJSONObject("payload").optString("text"))
                        }
                        chatSnapshots.forEach { fresh ->
                            val index = chatTurns.indexOfFirst { it.getString("task_id") == fresh.getString("task_id") }
                            if (index >= 0) chatTurns[index] = fresh
                        }
                        renderChat()
                    }
                    if (selectedTask()?.optString("task_id") == selectedId) {
                        // Show the daemon's activity as it happens: "Reading: …", "Running: …", first words of output.
                        progressLines.lastOrNull { it.second.isNotBlank() }?.let { line ->
                            val friendly = conversational(line.first, line.second)?.replaceFirstChar { it.uppercase() } ?: line.second
                            showStatus(friendly.take(140)); avatar.phase = VoicePhase.THINKING
                        }
                        if (finished) { working = false; if (!speech.isListening && !speech.isSpeaking) setPhase(VoicePhase.IDLE) }
                        else maybeNarrate(progressLines)
                        snapshot?.let { showTask(it) }
                        if (foreground && polling && speakNew && speakingAllowed())
                            messages.lastOrNull()?.let { pendingReply = selectedId!! to it }
                    }
                    // With the push stream up, polling is only a safety net; without it, poll every second while a
                    // chat turn runs in front of the owner so its output still streams.
                    val streaming = chatOpen && chatTurns.any { it.getString("state") in activeStates }
                    if (foreground && polling) {
                        handler.removeCallbacks(poll)
                        handler.postDelayed(poll, when {
                            page.getBoolean("has_more") -> 100
                            streamLive -> 15_000
                            streaming -> 1000
                            else -> 3000
                        })
                    }
                }
            } catch (e: ApiFailure) {
                if (e.status == 410 && e.code == "cursor_expired") {
                    eventCursor.resync(e.details.getLong("resync_cursor")); origin.cursor = eventCursor.get()
                    val snapshot = loadTasks(api(origin)); ui(ticket) { replaceTasks(snapshot); if (foreground && polling) handler.post(poll) }
                } else { ui(ticket) { polling = false; connected = false; updateDestination() }; throw e }
            } catch (e: Exception) { ui(ticket) { polling = false; connected = false; updateDestination() }; throw e }
        }
    }
    override fun onStart() {
        super.onStart(); foreground = true
        TaskWatchService.stop(this)
        WakeWordService.onWake = { onWakeWord() }
        // The notification's Turn off changes the setting while the app is away; show the current value.
        val wakeEnabled = preferences.getBoolean(WakeWordService.PREF_ENABLED, false)
        if (::wakeWord.isInitialized && wakeWord.isChecked != wakeEnabled) wakeWord.isChecked = wakeEnabled
        if (preferences.getBoolean(WakeWordService.PREF_ENABLED, false) && !WakeWordService.running &&
            checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) WakeWordService.start(this)
        if (wakeLaunch) { wakeLaunch = false; greeted = true; handler.postDelayed({ onWakeWord() }, 700) }
        if (noteMode && !notePaused) { muteRecognizerTones(true); showStatus("Back to your note. Keep talking, or tap ✎ to finish."); handler.postDelayed({ if (noteMode && !notePaused && foreground && !inDialog && !busy && !speech.isListening) requestListen() }, 1500) }
        // A result that arrived while the app was away is spoken once, then forgotten.
        preferences.getString(TaskWatchService.PREF_ANNOUNCE, null)?.let { raw ->
            preferences.edit().remove(TaskWatchService.PREF_ANNOUNCE).apply()
            try {
                val value = JSONObject(raw); working = false
                val text = "${value.optString("computer")} finished. ${value.optString("speech")}"
                lastReply = value.optString("speech"); output.text = text
                handler.postDelayed({ if (foreground && speakingAllowed()) speak(text) }, 1200)
                greeted = true
            } catch (_: Exception) { }
        }
        setTranscript(store.draft); reviewPending = store.draft.isNotBlank()
        if (::audio.isInitialized) { audio.resume(); audioChanged() }
        if (!busy) selectedComputer?.let { if (store.isPaired()) connect(it, false) }
        if (checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED &&
            !preferences.getBoolean("audio_permission_asked", false)) {
            preferences.edit().putBoolean("audio_permission_asked", true).apply()
            requestPermissions(arrayOf(Manifest.permission.BLUETOOTH_CONNECT), 2)
        } else if (checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) != PackageManager.PERMISSION_GRANTED &&
            !preferences.getBoolean("location_asked", false)) {
            preferences.edit().putBoolean("location_asked", true).apply()
            requestPermissions(arrayOf(Manifest.permission.ACCESS_COARSE_LOCATION), 3)
        } else fetchCity()
        greetingLabel.text = greetingText()
        handler.removeCallbacks(rotateSuggestion); handler.postDelayed(rotateSuggestion, 7000)
        if (!greeted) {
            greeted = true
            // One spoken hello per launch, after TTS has had a moment to initialise.
            handler.postDelayed({
                if (foreground && speakingAllowed() && !busy && !noteMode && !speech.isListening && !speech.isSpeaking && !inDialog)
                    speech.speak(spokenGreeting(), language.text.toString())
            }, 2500)
        }
    }
    override fun onStop() {
        WakeWordService.onWake = null; WakeWordService.hold(false)
        setShowWhenLocked(false); setTurnScreenOn(false)
        // Keep an in-flight task alive in a foreground service so its result is announced even if the app is away.
        if (working) guard.acceptedTaskId()?.let { id ->
            selectedComputer?.let { TaskWatchService.start(this, it.url, id, it.name, workingInstruction) }
        }
        foreground = false; polling = false; connected = false; epoch++; stopStream()
        waitingForRoute = false; pendingReply = null; handler.removeCallbacks(routeTimeout)
        handler.removeCallbacks(poll); handler.removeCallbacks(listenAgain); handler.removeCallbacks(rotateSuggestion)
        handsFree.isChecked = false; speech.pause(); audio.pause(); muteRecognizerTones(false)
        if (noteMode) showStatus("Your note is still open. Tap ✎ to finish it.")
        preferences.edit().putString("language", language.text.toString()).putBoolean("online", online.isChecked)
            .putBoolean("auto_speech", autoSpeech.isChecked).putBoolean("auto_send", autoSend.isChecked)
            .putBoolean("quiet", quiet.isChecked).apply()
        updateDestination(); super.onStop()
    }
    override fun onSaveInstanceState(state: Bundle) { state.putString("transcript", transcript.text.toString()); super.onSaveInstanceState(state) }
    override fun onDestroy() {
        epoch++; foreground = false; stopStream(); audioPicker?.dismiss(); dismissSheet(); speech.close(); audio.close(); executor.shutdownNow(); handler.removeCallbacksAndMessages(null); super.onDestroy()
    }
}

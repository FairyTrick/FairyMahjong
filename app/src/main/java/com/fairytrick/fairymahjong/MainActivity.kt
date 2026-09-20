package com.fairytrick.fairymahjong

import android.app.Activity
import android.content.SharedPreferences
import android.content.pm.ActivityInfo
import android.content.res.Configuration
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.window.OnBackInvokedCallback
import android.window.OnBackInvokedDispatcher
import java.util.concurrent.Executors
import java.util.concurrent.Future
import java.util.concurrent.atomic.AtomicBoolean

// An optional onboarding preference with the wrong stored type must not prevent startup.
internal fun hasSeenInstructions(preferences: SharedPreferences): Boolean = preferences.all["seen"] == true

class MainActivity : Activity() {
    private val muted = Color.rgb(230, 238, 210)
    private var game: MahjongGame? = null
    // Round-trip the retired setting for save compatibility; it no longer controls feedback.
    private var legacyHapticsSetting = true
    private var boardOrientation = BoardOrientation.PORTRAIT
    private var difficulty = GameDifficulty.NORMAL
    private var instructionsOpen = false
    private var instructionsView: HowToPlayView? = null
    private var instructionsBackCallback: OnBackInvokedCallback? = null
    private lateinit var screen: FrameLayout
    private lateinit var gameLayer: FrameLayout
    private var resumed = false
    private val haptics = GameHaptics { resumed && !isFinishing && !isDestroyed }
    private var saveRequest = 0L
    private lateinit var board: GardenBoardView
    private var boardViewport: GardenBoardViewport? = null
    private lateinit var message: TextView
    private lateinit var saveMessage: TextView
    private lateinit var controls: MeadowHudView
    private lateinit var motion: MeadowMotionView
    private val hintButton get() = controls.hintButton
    private val store: GameStore get() = (application as MahjongApplication).gameStore
    private var reportedReady = false
    private val hintEngine = HintEngine()
    private val hintWorker = Executors.newSingleThreadExecutor { task ->
        Thread(task, "mahjong-hint").apply { isDaemon = true }
    }
    private var hintTask: Future<*>? = null
    private var hintCancellation: AtomicBoolean? = null
    private var hintRequest = 0L
    private var activeHint: GameHint? = null
    private var findingHint = false
    private var hintNotice: Int? = null
    private var hintBudget = 200_000

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        instructionsOpen = savedInstanceState?.getBoolean("instructions_open") ?: false
        buildInterface()
        store.load { loaded ->
            if (isDestroyed || isFinishing) return@load
            game = MahjongGame(loaded.game.board)
            legacyHapticsSetting = loaded.game.hapticsEnabled
            boardOrientation = loaded.game.orientation
            difficulty = loaded.game.difficulty
            applyOrientation()
            render()
            when {
                !loaded.saveAvailable -> showSaveError()
                loaded.recoveredUnreadableSave -> showSaveNotice(R.string.save_recovered)
                loaded.migratedLegacySave -> showSaveNotice(R.string.save_migrated)
            }
            if (instructionsOpen || !hasSeenInstructions(getSharedPreferences("instructions", MODE_PRIVATE))) {
                showInstructions()
            }
        }
    }

    private fun buildInterface() {
        cancelMotion()
        val frame = FrameLayout(this)
        gameLayer = frame
        screen = FrameLayout(this).apply {
            background = MeadowBackground(this@MainActivity)
            addView(frame, FrameLayout.LayoutParams(-1, -1))
        }
        instructionsView = null
        val config = resources.configuration
        val portrait = config.orientation != Configuration.ORIENTATION_LANDSCAPE
        val content = LinearLayout(this).apply {
            orientation = if (portrait) LinearLayout.VERTICAL else LinearLayout.HORIZONTAL
            setPadding(dp(8), dp(8), dp(8), dp(10))
            gravity = Gravity.CENTER_VERTICAL
        }
        frame.addView(content, FrameLayout.LayoutParams(-1, -1))
        controls = MeadowHudView(this, ::requestHint, ::startNewBoard, ::toggleOrientation, ::openInstructions, ::toggleDifficulty)
        board = GardenBoardView(this) { index -> pickTile(index) }
        motion = MeadowMotionView(this)
        boardViewport = null
        if (portrait) {
            content.addView(controls.actionBar, LinearLayout.LayoutParams(-1, -2))
            val scroll = GardenBoardViewport(this)
            boardViewport = scroll
            scroll.setOnScrollChangeListener { _, _, _, _, _ -> cancelMotion() }
            scroll.addView(board, FrameLayout.LayoutParams(-1, -2))
            content.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
            content.addView(controls, LinearLayout.LayoutParams(-1, -2))
        } else {
            content.addView(controls.actionBar, LinearLayout.LayoutParams(-2, -1))
            content.addView(board, LinearLayout.LayoutParams(0, -1, 1f))
            content.addView(controls, LinearLayout.LayoutParams(dp(MeadowHudView.SIDE_HAND_WIDTH_DP), -1))
        }
        // Feedback overlays the meadow only when it is needed, without shifting the board.
        message = text(getString(R.string.opening_garden), 14f, muted).apply {
            gravity = Gravity.CENTER
            background = rounded(0xDF183527.toInt(), 12)
            setPadding(dp(14), dp(10), dp(14), dp(10))
            accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
        }
        saveMessage = text("", 12f, Color.rgb(255, 227, 168)).apply {
            visibility = View.GONE
            gravity = Gravity.CENTER
            maxLines = 2
            ellipsize = android.text.TextUtils.TruncateAt.END
            background = rounded(0xDF183527.toInt(), 8)
            setPadding(dp(10), dp(6), dp(10), dp(6))
            accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
        }
        val notices = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            addView(message)
            addView(saveMessage)
        }
        frame.addView(notices, FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM or Gravity.START).apply {
            leftMargin = dp(24 + if (portrait) 0 else MeadowHudView.ACTION_BAR_SIZE_DP)
            rightMargin = dp(24 + if (portrait) 0 else MeadowHudView.SIDE_HAND_WIDTH_DP)
        })
        controls.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ ->
            val params = notices.layoutParams as FrameLayout.LayoutParams
            val margin = dp(28) + if (portrait) controls.height else 0
            if (params.bottomMargin != margin) {
                params.bottomMargin = margin
                notices.layoutParams = params
            }
        }
        controls.actionBar.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ ->
            if (!portrait) {
                val params = notices.layoutParams as FrameLayout.LayoutParams
                val margin = dp(24) + controls.actionBar.width
                if (params.leftMargin != margin) {
                    params.leftMargin = margin
                    notices.layoutParams = params
                }
            }
        }
        frame.addView(motion, FrameLayout.LayoutParams(-1, -1))
        setContentView(screen)
        // Installing the content creates the decor required by Window's insets controller.
        configureSystemBars()
        screen.setOnApplyWindowInsetsListener { view, insets ->
            // Reserve physical cutouts and the navigation gesture strip. The hidden
            // status bar's pull-down region does not need a full row of layout padding.
            if (Build.VERSION.SDK_INT >= 30) {
                val cutout = insets.getInsets(WindowInsets.Type.displayCutout())
                val gestures = insets.getInsets(WindowInsets.Type.mandatorySystemGestures())
                view.setPadding(cutout.left, cutout.top, cutout.right, maxOf(cutout.bottom, gestures.bottom))
            } else if (Build.VERSION.SDK_INT >= 28) {
                val cutout = insets.displayCutout
                view.setPadding(cutout?.safeInsetLeft ?: 0, cutout?.safeInsetTop ?: 0,
                    cutout?.safeInsetRight ?: 0, cutout?.safeInsetBottom ?: 0)
            }
            insets
        }
        screen.requestApplyInsets()
        if (instructionsOpen) showInstructions()
        hideSystemBars()
    }

    @Suppress("DEPRECATION")
    private fun configureSystemBars() {
        window.statusBarColor = Color.TRANSPARENT
        window.navigationBarColor = Color.TRANSPARENT
        if (Build.VERSION.SDK_INT >= 28) {
            window.attributes = window.attributes.apply {
                layoutInDisplayCutoutMode = if (Build.VERSION.SDK_INT >= 30)
                    WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
                else WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
            }
        }
        if (Build.VERSION.SDK_INT >= 29) {
            window.isStatusBarContrastEnforced = false
            window.isNavigationBarContrastEnforced = false
        }
        if (Build.VERSION.SDK_INT >= 30) {
            window.setDecorFitsSystemWindows(false)
            window.insetsController?.setSystemBarsAppearance(
                0,
                WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS or
                    WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS,
            )
        } else {
            window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_LAYOUT_STABLE or
                View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
        }
    }

    @Suppress("DEPRECATION")
    private fun hideSystemBars() {
        if (Build.VERSION.SDK_INT >= 30) {
            window.insetsController?.apply {
                systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
                hide(WindowInsets.Type.systemBars())
            }
        } else {
            window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or
                View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
                View.SYSTEM_UI_FLAG_LAYOUT_STABLE or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or
                View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
        }
    }

    private fun pickTile(index: Int) {
        if (!resumed || !hasWindowFocus() || instructionsOpen) return
        val current = game ?: return
        if (!current.isPickable(index)) return
        cancelMotion()
        val capture = motion.capturePick(board.tileView(index), controls.handSlots(), current, index)
        val previousHint = activeHint
        val result = current.pickTile(index)
        if (result == PickResult.IGNORED) return
        invalidateHint()
        // Every prefix of this stored continuation was verified to retain a full win.
        if (previousHint?.nextPick == index && result != PickResult.MATCHED &&
            previousHint.picks.size > 1
        ) {
            activeHint = previousHint.copy(
                picks = previousHint.picks.drop(1), solution = previousHint.solution.drop(1),
            )
        }
        GameHapticEvent.forPick(result, current.isComplete, current.isGameOver)?.let {
            haptics.play(board, it)
        }
        render()
        persist()
        motion.playPick(capture, controls.handSlots(), current)
    }

    private fun openInstructions() {
        if (game == null || !resumed || instructionsOpen || !hasWindowFocus()) return
        showInstructions()
    }

    private fun showInstructions() {
        cancelMotion()
        // Reading the guide never changes accepted picks or a displayed hint.
        // Stop only an unfinished search, which has no result to preserve.
        if (findingHint) {
            invalidateHint()
            render()
        }
        instructionsOpen = true
        gameLayer.visibility = View.GONE
        gameLayer.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
        if (instructionsView == null) {
            instructionsView = HowToPlayView(this, ::hideInstructions).also {
                screen.addView(it, FrameLayout.LayoutParams(-1, -1))
            }
        }
        updateInstructionsBackHandling()
    }

    private fun hideInstructions() {
        if (!instructionsOpen) return
        instructionsOpen = false
        getSharedPreferences("instructions", MODE_PRIVATE).edit().putBoolean("seen", true).apply()
        instructionsView?.let(screen::removeView)
        instructionsView = null
        gameLayer.visibility = View.VISIBLE
        gameLayer.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_AUTO
        updateInstructionsBackHandling()
        render()
    }

    private fun updateInstructionsBackHandling() {
        if (Build.VERSION.SDK_INT < 33) return
        if (instructionsOpen && instructionsBackCallback == null) {
            val callback = OnBackInvokedCallback { hideInstructions() }
            instructionsBackCallback = callback
            onBackInvokedDispatcher.registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT, callback)
        } else if (!instructionsOpen) {
            instructionsBackCallback?.let(onBackInvokedDispatcher::unregisterOnBackInvokedCallback)
            instructionsBackCallback = null
        }
    }

    // Android 26–32 fallback; newer versions use the registered back callback above.
    @android.annotation.SuppressLint("GestureBackNavigation")
    @Suppress("DEPRECATION", "OVERRIDE_DEPRECATION")
    override fun onBackPressed() {
        if (instructionsOpen) hideInstructions() else super.onBackPressed()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putBoolean("instructions_open", instructionsOpen)
        super.onSaveInstanceState(outState)
    }

    private fun startNewBoard() {
        if (game == null || !resumed || instructionsOpen || !hasWindowFocus()) return
        cancelMotion()
        invalidateHint()
        game = MahjongGame.newGame(orientation = boardOrientation, difficulty = difficulty)
        boardViewport?.scrollTo(0, 0)
        render()
        persist()
        haptics.play(controls.newBoardButton, GameHapticEvent.DEAL)
    }

    private fun toggleOrientation() {
        if (game == null || !resumed || instructionsOpen || !hasWindowFocus() || !controls.rotateButton.isEnabled) return
        cancelMotion()
        invalidateHint()
        boardOrientation = boardOrientation.toggled()
        game = MahjongGame.newGame(orientation = boardOrientation, difficulty = difficulty)
        persist()
        haptics.play(controls.rotateButton, GameHapticEvent.DEAL)
        render()
        applyOrientation()
    }

    private fun toggleDifficulty() {
        if (game == null || !resumed || instructionsOpen || !hasWindowFocus()) return
        cancelMotion()
        invalidateHint()
        difficulty = difficulty.next()
        game = MahjongGame.newGame(orientation = boardOrientation, difficulty = difficulty)
        boardViewport?.scrollTo(0, 0)
        render()
        persist()
        haptics.play(controls.difficultyButton, GameHapticEvent.DEAL)
    }

    private fun applyOrientation() {
        requestedOrientation = if (boardOrientation == BoardOrientation.LANDSCAPE)
            ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE else ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        if (!::controls.isInitialized) return
        val savedNotice = saveMessage.text.takeIf { saveMessage.visibility == View.VISIBLE }
        // The toggle creates the deal once. Resizing, rotation delivery and recreation never do.
        buildInterface()
        render()
        if (savedNotice != null) {
            saveMessage.text = savedNotice
            saveMessage.visibility = View.VISIBLE
        }
    }

    private fun render() {
        val current = game ?: return
        board.render(current, activeHint)
        controls.render(current, activeHint, findingHint, boardOrientation, difficulty)
        message.text = when {
            current.isComplete -> getString(R.string.board_complete)
            current.isGameOver -> getString(R.string.hand_full)
            findingHint -> getString(R.string.hint_searching)
            activeHint != null -> {
                val hint = requireNotNull(activeHint)
                val instruction = when {
                    hint.picks.size == 1 -> R.string.hint_match_instruction
                    hint.picks.size == 2 && current.faceAt(hint.picks[0]) == current.faceAt(hint.picks[1]) ->
                        R.string.hint_pair_instruction
                    else -> R.string.hint_steps_instruction
                }
                getString(instruction,
                    TileCatalog.face(current.faceAt(hint.nextPick)).name,
                    TileCatalog.face(current.faceAt(hint.matchingTiles.first())).name)
            }
            hintNotice != null -> getString(requireNotNull(hintNotice))
            else -> ""
        }
        message.setTextColor(if (current.isGameOver) Color.rgb(255, 215, 166) else muted)
        val status = getString(when {
            current.isComplete -> R.string.status_complete
            current.isGameOver -> R.string.status_game_over
            else -> R.string.status_playing
        })
        controls.contentDescription = if (message.text.isNotEmpty()) "$status. ${message.text}" else status
        message.visibility = if (current.isComplete || current.isGameOver || hintNotice != null)
            View.VISIBLE else View.GONE
        if (!reportedReady) {
            reportedReady = true
            board.post { if (!isDestroyed && !isFinishing) reportFullyDrawn() }
        }
    }

    private fun requestHint() {
        val current = game ?: return
        if (!resumed || !hasWindowFocus() || instructionsOpen || findingHint || current.isComplete || current.isGameOver) return
        // A held match and its blue border must be visible as soon as the hint arrives.
        cancelMotion()
        val snapshot = current.snapshot()
        invalidateHint(resetBudget = false)
        val request = hintRequest
        val cancelled = AtomicBoolean(false)
        hintCancellation = cancelled
        val budget = hintBudget
        findingHint = true
        render()
        hintTask = hintWorker.submit {
            val result = try {
                hintEngine.findHint(snapshot, maxNodes = budget) {
                    cancelled.get() || Thread.currentThread().isInterrupted
                }
            } catch (error: Exception) {
                android.util.Log.e("FairyMahjong", "Hint search failed", error)
                null
            }
            runOnUiThread {
                // A late answer must never highlight a different board or a later turn.
                if (cancelled.get() || request != hintRequest || !resumed || isDestroyed || isFinishing ||
                    game?.snapshot() != snapshot
                ) return@runOnUiThread
                hintTask = null
                hintCancellation = null
                findingHint = false
                when (result) {
                    is HintResult.Found -> activeHint = result.hint
                    HintResult.Unwinnable -> hintNotice = R.string.hint_unwinnable
                    HintResult.SearchLimit -> {
                        hintNotice = if (hintBudget < 800_000) R.string.hint_search_limit else R.string.hint_unconfirmed
                        hintBudget = (hintBudget * 2).coerceAtMost(800_000)
                    }
                    HintResult.Complete, HintResult.Cancelled -> Unit
                    null -> hintNotice = R.string.hint_unconfirmed
                }
                render()
                if (result is HintResult.Found) {
                    haptics.play(hintButton, GameHapticEvent.HINT)
                    board.playHintBreath()
                }
            }
        }
    }

    private fun invalidateHint(resetBudget: Boolean = true) {
        hintRequest += 1
        hintCancellation?.set(true)
        hintTask?.cancel(true)
        hintTask = null
        hintCancellation = null
        findingHint = false
        activeHint = null
        hintNotice = null
        if (resetBudget) hintBudget = 200_000
    }

    override fun onResume() {
        super.onResume()
        resumed = true
    }

    override fun onPause() {
        resumed = false
        cancelMotion()
        haptics.cancel()
        invalidateHint()
        if (game != null && ::board.isInitialized) render()
        super.onPause()
    }

    override fun onDestroy() {
        cancelMotion()
        haptics.cancel()
        invalidateHint()
        hintWorker.shutdownNow()
        if (Build.VERSION.SDK_INT >= 33) {
            instructionsBackCallback?.let(onBackInvokedDispatcher::unregisterOnBackInvokedCallback)
            instructionsBackCallback = null
        }
        super.onDestroy()
    }

    private fun persist() {
        val snapshot = game?.snapshot() ?: return
        val request = ++saveRequest
        store.save(SavedGame(snapshot, legacyHapticsSetting, boardOrientation, difficulty)) { succeeded ->
            if (isDestroyed || isFinishing || request != saveRequest) return@save
            if (succeeded) saveMessage.visibility = View.GONE else showSaveError()
        }
    }

    private fun showSaveNotice(messageId: Int) {
        saveMessage.setText(messageId)
        saveMessage.visibility = View.VISIBLE
    }
    private fun showSaveError() = showSaveNotice(R.string.save_failed)
    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemBars()
        else {
            haptics.cancel()
            cancelMotion()
        }
    }
    private fun cancelMotion() {
        if (::motion.isInitialized) motion.cancel()
        if (::board.isInitialized) board.cancelMotion()
    }
    private fun rounded(color: Int, radius: Int, stroke: Int? = null) = GradientDrawable().apply {
        setColor(color)
        cornerRadius = dp(radius).toFloat()
        stroke?.let { setStroke(dp(1), it) }
    }
    private fun text(value: String, size: Float, color: Int) = TextView(this).apply {
        text = value
        textSize = size
        setTextColor(color)
        setShadowLayer(dp(1).toFloat(), 0f, dp(1).toFloat(), 0xA0001B0C.toInt())
        includeFontPadding = false
    }
    private fun dp(value: Int): Int = (value * resources.displayMetrics.density + 0.5f).toInt()
}

package ai.chintu

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Handler
import android.os.Looper

/** API 31+ foreground communication routing. Call public methods on the main thread. */
class AudioRouteController(
    context: Context,
    private val onChanged: () -> Unit,
    private val onStatus: (String) -> Unit
) {
    data class Route(
        val id: Int,
        val label: String,
        val detail: String,
        val selectable: Boolean,
        val selected: Boolean,
        /** SCO/BLE headset; media-only Bluetooth outputs are not microphone routes. */
        val bluetooth: Boolean
    )

    private val context = context.applicationContext
    private val audio = requireNotNull(this.context.getSystemService(AudioManager::class.java)) { "Android audio service unavailable." }
    private val main = Handler(Looper.getMainLooper())
    private var activeDevice: AudioDeviceInfo? = null
    // Only a deliberate UI choice is explicit. Automatic phone fallback never becomes one.
    private var explicitRouteId: Int? = null
    var requestedRouteId: Int? = null
        private set
    private var modeOwned = false
    private var paused = false
    private var closed = false
    private var deviceListenerRegistered = false
    private var routeListenerRegistered = false

    private val devices = object : AudioDeviceCallback() {
        override fun onAudioDevicesAdded(addedDevices: Array<out AudioDeviceInfo>?) {
            if (!closed && !paused) {
                notifyChanged()
                choosePreferredRoute()
            }
        }
        override fun onAudioDevicesRemoved(removedDevices: Array<out AudioDeviceInfo>?) {
            if (closed || paused) return
            if (removedDevices?.any { it.id == activeDevice?.id } == true) {
                activeDevice = null
            }
            // Let the UI stop current recognition before a different input route is requested.
            notifyChanged()
            choosePreferredRoute()
        }
    }
    private val communication = AudioManager.OnCommunicationDeviceChangedListener { device ->
        if (!closed && !paused) {
            activeDevice = device
            val route = activeRoute()
            val requested = requestedRouteId
            val active = route?.let(::label) ?: "Choose an audio device"
            onStatus(if (requested != null && requested != route?.id)
                "Switching audio…"
            else "Audio: $active")
            notifyChanged()
        }
    }

    init {
        requireMainThread()
        activeDevice = readActiveDevice()
        registerListeners()
    }

    private fun requireMainThread() {
        check(Looper.myLooper() == Looper.getMainLooper()) { "Audio routing must run on the main thread." }
    }
    private fun hasBluetoothPermission(): Boolean = context.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) ==
        PackageManager.PERMISSION_GRANTED
    private fun isHeadset(device: AudioDeviceInfo): Boolean = device.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO ||
        device.type == AudioDeviceInfo.TYPE_BLE_HEADSET
    private fun isBluetooth(device: AudioDeviceInfo): Boolean = isHeadset(device) ||
        device.type == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP || device.type == AudioDeviceInfo.TYPE_BLE_SPEAKER ||
        device.type == AudioDeviceInfo.TYPE_HEARING_AID
    /** Only devices that carry both a microphone and a speaker are voice routes; the earpiece is folded into "Phone". */
    private fun supportsVoice(device: AudioDeviceInfo): Boolean = when (device.type) {
        AudioDeviceInfo.TYPE_BUILTIN_SPEAKER, AudioDeviceInfo.TYPE_BLUETOOTH_SCO, AudioDeviceInfo.TYPE_BLE_HEADSET,
        AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioDeviceInfo.TYPE_USB_HEADSET -> true
        else -> false
    }
    private fun available(): List<AudioDeviceInfo> = try {
        audio.availableCommunicationDevices.filter { supportsVoice(it) && (!isBluetooth(it) || hasBluetoothPermission()) }
    } catch (_: SecurityException) { emptyList() }
    private fun readActiveDevice(): AudioDeviceInfo? = try { audio.communicationDevice }
        catch (_: SecurityException) { null }
    /** Android may report a headset's A2DP endpoint while its SCO endpoint was requested; same address, same device. */
    private fun isPhone(device: AudioDeviceInfo): Boolean =
        device.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER || device.type == AudioDeviceInfo.TYPE_BUILTIN_EARPIECE
    /** Earpiece and speaker are the same physical "Phone" route; Android often reports the earpiece as active. */
    private fun sameDevice(a: AudioDeviceInfo, b: AudioDeviceInfo): Boolean =
        a.id == b.id || (a.address.isNotBlank() && a.address == b.address) || (isPhone(a) && isPhone(b))
    /** The voice route that corresponds to the platform's active communication device, if any. */
    private fun activeRoute(): AudioDeviceInfo? {
        val active = activeDevice ?: return null
        return available().firstOrNull { sameDevice(it, active) }
    }
    private fun notifyChanged() { if (!closed) onChanged() }

    private fun registerListeners() {
        if (!deviceListenerRegistered) try {
            audio.registerAudioDeviceCallback(devices, main)
            deviceListenerRegistered = true
        } catch (_: SecurityException) { onStatus("Audio devices unavailable. Try Refresh.") }
        if (!routeListenerRegistered) try {
            audio.addOnCommunicationDeviceChangedListener(context.mainExecutor, communication)
            routeListenerRegistered = true
        } catch (_: SecurityException) { onStatus("Audio switching is unavailable. Try Bluetooth settings.") }
    }

    fun routes(): List<Route> {
        requireMainThread()
        if (closed) return emptyList()
        // Media-only outputs (A2DP speakers, HDMI, headphones without a mic) are deliberately absent.
        val connected = available().distinctBy { it.id }
        val activeId = activeRoute()?.id
        return connected.map { device ->
            val active = !paused && device.id == activeId
            val detail = when {
                active -> "Selected for voice audio"
                device.id == requestedRouteId -> "Switching…"
                else -> "Microphone and speaker"
            }
            Route(device.id, label(device), detail, true, active, isHeadset(device))
        }.sortedWith(compareByDescending<Route> { it.bluetooth }.thenBy { it.label })
    }

    /** True only for a currently available, platform-reported SCO/BLE headset route. */
    fun isBluetoothActive(): Boolean {
        requireMainThread()
        if (closed || paused || !hasBluetoothPermission()) return false
        val route = activeRoute() ?: return false
        return isHeadset(route) && (requestedRouteId == null || requestedRouteId == route.id)
    }
    /** Capture may begin only after the selected available route matches any pending request. */
    fun routeReady(): Boolean {
        requireMainThread()
        if (closed || paused) return false
        val route = activeRoute() ?: return false
        return requestedRouteId == null || requestedRouteId == route.id
    }
    fun activeBluetoothName(): String? = if (isBluetoothActive()) activeRoute()?.let(::label) else null
    fun currentRouteLabel(): String {
        requireMainThread()
        return routes().firstOrNull { it.selected }?.label ?: "Switching audio…"
    }

    /** A true return means request accepted; activation is confirmed by the route listener. */
    fun select(id: Int): Boolean = selectRoute(id, manual = true)

    private fun selectRoute(id: Int, manual: Boolean): Boolean {
        requireMainThread()
        if (closed) return false
        paused = false
        val device = available().firstOrNull { it.id == id }
        if (device == null) {
            onStatus("That device is unavailable for voice audio. Choose another device or open Bluetooth settings.")
            notifyChanged()
            return false
        }
        if (activeRoute()?.id == id && (requestedRouteId == null || requestedRouteId == id)) {
            if (manual) explicitRouteId = id
            onStatus("Audio: ${label(device)}")
            notifyChanged()
            return true
        }
        if (requestedRouteId == id) {
            if (manual) explicitRouteId = id
            onStatus("Switching audio…")
            return true
        }
        val ownedBefore = modeOwned
        return try {
            audio.mode = AudioManager.MODE_IN_COMMUNICATION
            modeOwned = true
            if (!audio.setCommunicationDevice(device)) {
                if (!ownedBefore) releaseMode()
                onStatus("Couldn't switch audio. Refresh the device list and try again.")
                notifyChanged()
                false
            } else {
                if (manual) explicitRouteId = id
                requestedRouteId = id
                onStatus("Switching audio to ${label(device)}…")
                notifyChanged()
                true
            }
        } catch (_: SecurityException) {
            if (!ownedBefore) releaseMode()
            onStatus("Allow Nearby devices to use Bluetooth, or choose phone audio.")
            notifyChanged()
            false
        } catch (_: IllegalArgumentException) {
            if (!ownedBefore) releaseMode()
            onStatus("That device disconnected. Refresh and choose again.")
            notifyChanged()
            false
        }
    }

    private fun choosePreferredRoute() {
        if (closed || paused) return
        val devices = available()
        // An available explicit choice wins, including a deliberate phone selection.
        val explicit = devices.firstOrNull { it.id == explicitRouteId }
        if (explicit != null) {
            selectRoute(explicit.id, manual = false)
            return
        }
        val headsets = devices.filter(::isHeadset)
        val headset = headsets.firstOrNull { it.id == requestedRouteId }
            ?: headsets.firstOrNull { it.id == activeRoute()?.id }
            ?: headsets.firstOrNull()
        if (headset != null) {
            selectRoute(headset.id, manual = false)
            return
        }
        val speaker = devices.firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER }
        if (speaker != null) {
            // No explicit choice is recorded: a newly connected headset can be preferred later.
            val wasReady = activeRoute()?.id == speaker.id &&
                (requestedRouteId == null || requestedRouteId == speaker.id)
            if (selectRoute(speaker.id, manual = false))
                onStatus(if (wasReady) "Phone audio selected" else "Switching to phone audio…")
        } else {
            requestedRouteId = null
            try { audio.clearCommunicationDevice() } catch (_: SecurityException) { /* Already unavailable. */ }
            releaseMode()
            activeDevice = readActiveDevice()
            onStatus("Using phone audio.")
            notifyChanged()
        }
    }

    /** Restore an available explicit choice, otherwise prefer a connected headset, then phone. */
    fun resume() {
        requireMainThread()
        if (closed) return
        paused = false
        registerListeners()
        activeDevice = readActiveDevice()
        if (requestedRouteId != null && available().none { it.id == requestedRouteId }) requestedRouteId = null
        choosePreferredRoute()
        notifyChanged()
    }

    private fun releaseMode() {
        if (!modeOwned) return
        try { audio.mode = AudioManager.MODE_NORMAL } catch (_: SecurityException) { /* Permission was revoked. */ }
        modeOwned = false
    }
    fun pause() {
        requireMainThread()
        if (closed) return
        paused = true
        requestedRouteId = null
        activeDevice = null
        try { audio.clearCommunicationDevice() } catch (_: SecurityException) { /* Permission was revoked. */ }
        releaseMode()
        notifyChanged()
    }
    fun close() {
        requireMainThread()
        if (closed) return
        pause()
        closed = true
        if (deviceListenerRegistered) audio.unregisterAudioDeviceCallback(devices)
        if (routeListenerRegistered) audio.removeOnCommunicationDeviceChangedListener(communication)
        deviceListenerRegistered = false
        routeListenerRegistered = false
    }

    private fun label(device: AudioDeviceInfo): String {
        val kind = when (device.type) {
            AudioDeviceInfo.TYPE_BUILTIN_SPEAKER -> "Phone"
            AudioDeviceInfo.TYPE_BUILTIN_EARPIECE -> "Phone"
            AudioDeviceInfo.TYPE_BLUETOOTH_SCO -> "Bluetooth headset"
            AudioDeviceInfo.TYPE_BLE_HEADSET -> "Bluetooth LE headset"
            AudioDeviceInfo.TYPE_BLUETOOTH_A2DP -> "Bluetooth media output"
            AudioDeviceInfo.TYPE_BLE_SPEAKER -> "Bluetooth LE speaker"
            AudioDeviceInfo.TYPE_HEARING_AID -> "Hearing aid"
            AudioDeviceInfo.TYPE_WIRED_HEADSET -> "Wired headset"
            AudioDeviceInfo.TYPE_WIRED_HEADPHONES -> "Wired headphones"
            AudioDeviceInfo.TYPE_USB_HEADSET -> "USB headset"
            AudioDeviceInfo.TYPE_USB_DEVICE -> "USB audio"
            AudioDeviceInfo.TYPE_HDMI, AudioDeviceInfo.TYPE_HDMI_ARC -> "HDMI audio"
            else -> "Audio output"
        }
        val name = if (isBluetooth(device) && !hasBluetoothPermission()) "" else device.productName.toString().trim()
        return if (name.isBlank() || kind == "Phone") kind else "$kind · $name"
    }
}

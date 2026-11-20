//
//  play_device.swift
//  Plays an audio file on a named output device, regardless of the Mac's default output.
//
//  When the Mac's output is routed to BlackHole so the Simulator's voice can be recorded
//  cleanly, `afplay` would send the spoken prompts into BlackHole too: they would land in the
//  recording and never reach the microphone the Simulator listens on. This plays them on the
//  speakers instead, so they reach the session by acoustic coupling and stay out of the capture.
//
//  Usage: swift play_device.swift <file> "<output device name, e.g. MacBook Air Speakers>"
//

import AVFoundation
import CoreAudio
import Foundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8) ?? Data())
    exit(1)
}

func deviceID(named name: String) -> AudioDeviceID? {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDevices,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size) == noErr
    else { return nil }
    var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
    guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &ids) == noErr
    else { return nil }
    for id in ids {
        var nameAddress = AudioObjectPropertyAddress(
            mSelector: kAudioObjectPropertyName,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var cfName: Unmanaged<CFString>?
        var nameSize = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        if AudioObjectGetPropertyData(id, &nameAddress, 0, nil, &nameSize, &cfName) == noErr,
           let value = cfName?.takeRetainedValue() as String?, value == name {
            return id
        }
    }
    return nil
}

let args = CommandLine.arguments
guard args.count == 3 else { fail("usage: swift play_device.swift <file> \"<output device name>\"") }
guard var device = deviceID(named: args[2]) else { fail("no audio device named \"\(args[2])\"") }

let engine = AVAudioEngine()
guard let unit = engine.outputNode.audioUnit else { fail("output node has no audio unit") }
let status = AudioUnitSetProperty(
    unit, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0,
    &device, UInt32(MemoryLayout<AudioDeviceID>.size)
)
guard status == noErr else { fail("could not select \"\(args[2])\" (status \(status))") }

let file: AVAudioFile
do {
    file = try AVAudioFile(forReading: URL(fileURLWithPath: args[1]))
} catch {
    fail("cannot read \(args[1]): \(error)")
}
let player = AVAudioPlayerNode()
engine.attach(player)
engine.connect(player, to: engine.mainMixerNode, format: file.processingFormat)
do {
    try engine.start()
} catch {
    fail("cannot start output: \(error)")
}
let done = DispatchSemaphore(value: 0)
player.scheduleFile(file, at: nil) { done.signal() }
player.play()
done.wait()
// The completion fires when the last buffer is consumed, not heard; let the device drain.
Thread.sleep(forTimeInterval: 0.5)
engine.stop()

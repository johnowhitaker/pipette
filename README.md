# CNC Pipette

CAD: [link](https://cad.onshape.com/documents/42cf135ce4b1a1a34746c4ee/w/bec91f2a6df1f3690807754b/e/98be9c73e6b42ac109d81153?renderMode=0&uiState=697f9facc65afbdb14e9a4b1)

Main Parts: Dynamixel [servo](https://www.robotis.us/dynamixel-xl430-w250-t/) xl430 (the one I happened to have on hand), [waveshare board](https://www.amazon.com/dp/B0CTMM4LWK?_encoding=UTF8&th=1) to control it, 12V PSU, raspberry pi (optional, I've also run this from a laptop), Ender 3 V3 SE printer (variable, lots of old ender 3s for <$100 on FB marketplace). [Adjustable pipette](https://www.ebay.com/itm/296196251279) - I chose a 20-200uL one.

This repo has the code I use for controlling my CNC pipette. It's a 3D printed adapter that bolts on to the hotend of my 3D printer, and uses a servo to push on the pipette, sucking up and depositing liquid.

Besides some scripts used to figure out control of the servo and printer, the main parts in this repo are 1) the server you run on the pi/laptop that has the printer and control board plugged in and 2) a demo notebook showing how I move it around. 

This was quickly cobbled together - if in doubt, ask a friendly AI to take a look and adapt for your situation, or reach out to me with questions @johnowhitaker on X, etc. 

I'm still messing with the pipetting flow - by pushing out slightly more than you drew in you can ensure no drop remains, but too much and you get bubbles. If I wanted to be precise with this I'd calibrate by dropping hundreds of drops of different amounts on a sensitive scale. Since for now its use is artistic, I'm just going with 'yeah looks about right' :)

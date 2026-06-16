# Label Printing Flask App Requirements for VC-500W
I would like a label printing UI created with our usual flask/javascript (or typescript) formula. I need the following features:
Devices:
	- For now we only have one device (the VC-500W), but I could see getting more, or making this work with a printer.
UI:
    - Should have a tab based interface with (Print, Edit, Device, Settings, About)
       - Print - prints out and allows reseting of device
       - Edit - allows creating of images for printing out.
       - Device give more detailed device settings (per-device) with:
       	  - device status, firmware version, tape left, last printed, total prints, etc.
       	  - anything else you need
       - Settings - has collapsible sections for settings
       - About - has version number, memory free, etc.
Editing features.
	- Should show the status of my Vc-5002 printer and allow diagonstics (as far as possible)
	- Should know if I am using 25mm or 50mm tape (I think those are the options).
	- Should be display list based so I can edit and move things around.
	- Should allow me to start with a bitmap (jpg or png or gif)
	- Should allow me to shrink or expand and crop it as needed.
	- Should allow me to add a border of a given color and thickness
	- Should allow me to add text of common font, and color and scale it.
Nice to have (can defer these):
	- Should be able to diplay CJK characters and fonts as well,
	- Should allow me to change the Z-order
	- Should allow me to draw lines, squares, polygons (filled and nonfilled) with different edge colors 
	- Should allow me to move the above elements around, expand and contract them.
History:
	- Should keep a log of what I printed out so I can reload and modify it.
	- Should save the display lists in their own directories (I suppose), so we can reload them
	- Should be able to delete log entries.
	- Should be file based (I don't need a database for these things).
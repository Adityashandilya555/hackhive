<!-- Pasted verbatim by Adi in chat on 2026-10-15 (Honeywell PS4). -->

Project Title: Joint Optimization of Control-Panel Placement and Closed-Loop Cable Routing
IA
Project Definition: Develop an intelligent system that uses residential floor-plan geometry to jointly determine:
• The number and locations of control panels
• The rooms and devices assigned to each panel
• The order in which devices are connected
• The physical cable route for each closed loop
One device must be placed inside each eligible living room, bedroom, kitchen, and storage room. Each panel can support a maximum of:
• 100 m² of assigned eligible-room area
• 20 assigned devices
Rooms assigned to a panel must form a contiguous territory, and every device must be assigned to exactly one panel.
Each panel must have one closed, non-branching loop:
Panel P → Device 1 → Device 2 → … → Device N → Same Panel P
The cable loop must originate at the panel, visit every assigned device exactly once, and terminate back at the same panel. Cable routes may pass between rooms only
through doors and open passages. Cables must not pass through walls, windows, or external areas, and must remain within the building’s internal boundary.
Open-ended routes, branches, skipped or repeated devices, and routes ending at another panel are invalid.
Major Work Elements:
• ResPlan geometry interpretation and eligible-room identification
• Fixed device placement within eligible rooms
• Capacity-constrained panel placement and room assignment
• Contiguous panel-territory generation
• Closed-loop device sequencing
• Obstacle-aware routing through doors and open passages only
• Wall, window, and external-area avoidance
• Optimization of panel count, cable length, crossings, overlaps, and bends
• Design validation and floor-plan visualization
Required Skills: Python, computational geometry, graph algorithms, optimization, pathfinding, travelling-salesperson methods, and data visualization
Resources Needed:
ResPlan on Kaggle and the GitHub repository (https://www.kaggle.com/datasets/resplan/resplan)
Expected Outcome: A working prototype that processes an unseen ResPlan floor plan and generates a visual overlay showing room-to-panel assignments, devices,
panels, connection order, and complete closed-loop cable routes. The visualization must clearly show that routes pass through doors or open passages, avoid walls and
windows, and return to the originating panel. A matching JSON output must support validation and scoring.

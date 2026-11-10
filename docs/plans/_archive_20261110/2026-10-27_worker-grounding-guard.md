# Worker Grounding Guard
> STATUS: frozen（英文安全机制描述稿；无工单/实现/引用，2026-10-31 W2 复核后转只读）



## Overview

The Worker Grounding Guard is a safety mechanism designed to prevent unauthorised operations within the Zero Boundary System.

## Features

- Monitors for unauthorised commands.
- Immediately halts operations when a threat is detected.
- Provides detailed logs for auditing purposes.

## Setup

To set up the Worker Grounding Guard, follow these steps:

1. Ensure that the Zero Boundary System is running.
2. Run the following command to initiate the Worker Grounding Guard:
   ```bash
   python -X utf8 tools/mission_worker.py run --mission <id> --task <tid>
   ```
3. Verify that the guard is active by checking the system logs.

## Usage

The Worker Grounding Guard operates silently in the background, continuously monitoring for potential threats.

## Safety Boundaries

- The guard is configured to detect and halt any operations that exceed the defined safety boundaries.
- All operations within the system must comply with the safety policies.

## Rollback

In case of any issues, follow these steps to rollback changes:

1. Stop the Worker Grounding Guard by running the following command:
   ```bash
   python -X utf8 tools/mission_worker.py run --mission <id> --task <tid> --dry-run
   ```
2. Review the system logs to identify the issue.
3. Apply the necessary fixes to resolve the issue.

4. Restart the Worker Grounding Guard with the correct configuration.

This document provides a comprehensive guide on how to set up, use, and maintain the Worker Grounding Guard within the Zero Boundary System.
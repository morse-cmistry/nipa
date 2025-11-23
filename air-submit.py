#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

"""CLI tool for submitting patches to AIR and monitoring review status"""

import argparse
import json
import sys
import time
import requests
from typing import List, Optional


# ANSI color codes
class Colors:
    RESET = '\033[0m'
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'


def colorize(text: str, color: str) -> str:
    """Add color to text if output is a TTY

    Args:
        text: Text to colorize
        color: Color code

    Returns:
        Colored text if stdout is a TTY, otherwise plain text
    """
    if sys.stdout.isatty():
        return f"{color}{text}{Colors.RESET}"
    return text


def read_patch_files(patch_files: List[str]) -> List[str]:
    """Read patch content from files

    Args:
        patch_files: List of paths to patch files

    Returns:
        List of patch contents
    """
    patches = []
    for path in patch_files:
        try:
            with open(path, 'r') as f:
                patches.append(f.read())
        except FileNotFoundError:
            print(f"Error: Patch file not found: {path}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error reading patch file {path}: {e}", file=sys.stderr)
            sys.exit(1)
    return patches


def submit_review(url: str, token: str, tree: str, branch: Optional[str],
                  patches: Optional[List[str]] = None,
                  patchwork_series_id: Optional[int] = None) -> str:
    """Submit patches for review

    Args:
        url: AIR service URL
        token: API token
        tree: Git tree name
        branch: Optional branch name
        patches: List of patch contents (or None if using patchwork)
        patchwork_series_id: Patchwork series ID (or None if using patches)

    Returns:
        Review ID
    """
    api_url = f"{url.rstrip('/')}/api/review"

    payload = {
        'token': token,
        'tree': tree,
    }

    if patches:
        payload['patches'] = patches
    elif patchwork_series_id:
        payload['patchwork_series_id'] = patchwork_series_id
    else:
        print("Error: Either patches or patchwork_series_id must be provided", file=sys.stderr)
        sys.exit(1)

    if branch:
        payload['branch'] = branch

    try:
        response = requests.post(api_url, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data['review_id']
    except requests.exceptions.RequestException as e:
        print(f"Error submitting review: {e}", file=sys.stderr)
        sys.exit(1)
    except (KeyError, json.JSONDecodeError) as e:
        print(f"Error parsing response: {e}", file=sys.stderr)
        sys.exit(1)


def get_review_status(url: str, token: str, review_id: str,
                      fmt: Optional[str] = None) -> dict:
    """Get review status

    Args:
        url: AIR service URL
        token: API token
        review_id: Review ID
        fmt: Optional format (json, markup, inline)

    Returns:
        Review status dictionary
    """
    api_url = f"{url.rstrip('/')}/api/review"
    params = {
        'id': review_id,
        'token': token
    }

    if fmt:
        params['format'] = fmt

    try:
        response = requests.get(api_url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error querying review status: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error parsing response: {e}", file=sys.stderr)
        sys.exit(1)


def format_status_line(status: dict) -> str:
    """Format one-line status summary with color

    Args:
        status: Review status dictionary

    Returns:
        Formatted status string with color codes
    """
    state = status['status']
    patch_count = status.get('patch_count', 0)
    completed = status.get('completed_patches', 0)

    if state == 'queued':
        queue_len = status.get('queue-len', '?')
        status_text = f"Status: {colorize('queued', Colors.CYAN)} (patches ahead: {queue_len})"
        return status_text
    elif state == 'in-progress':
        if patch_count > 0:
            progress = f"{completed}/{patch_count}"
            status_text = f"Status: {colorize('in-progress', Colors.YELLOW)} ({progress} patches completed)"
        else:
            status_text = f"Status: {colorize('in-progress', Colors.YELLOW)} (setting up...)"
        return status_text
    elif state == 'done':
        status_text = f"Status: {colorize('done', Colors.GREEN + Colors.BOLD)} ({patch_count} patches reviewed)"
        return status_text
    elif state == 'error':
        msg = status.get('message', 'unknown error')
        status_text = f"Status: {colorize('error', Colors.RED + Colors.BOLD)} - {msg}"
        return status_text
    else:
        return f"Status: {state}"


def print_reviews(reviews: List[Optional[str]], patch_count: int):
    """Print review results

    Args:
        reviews: List of review contents (may contain None for failed patches)
        patch_count: Total number of patches
    """
    for i, review in enumerate(reviews, 1):
        separator = '='*80
        patch_header = f"PATCH {i}/{patch_count}"

        print(f"\n{separator}")
        print(colorize(patch_header, Colors.BOLD + Colors.BLUE))
        print(separator)

        if review is None:
            print(colorize("No review comments", Colors.GREEN))
        else:
            print(review)


def main():
    parser = argparse.ArgumentParser(
        description='Submit patches to AIR for review or check existing review status',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Submit patch files
  %(prog)s --url https://example.com/air --token mytoken --tree netdev/net-next 0001-fix.patch 0002-feat.patch

  # Submit patchwork series
  %(prog)s --url https://example.com/air --token mytoken --tree netdev/net-next --pw-series 1026553

  # Check existing review
  %(prog)s --url https://example.com/air --token mytoken --review-id abc-123-def

  # Submit and exit without polling
  %(prog)s --url https://example.com/air --token mytoken --tree netdev/net-next --no-wait 0001-fix.patch

  # Check once and get results if done
  %(prog)s --url https://example.com/air --token mytoken --review-id abc-123-def --no-wait --format markup
        """
    )

    parser.add_argument('--url', required=True,
                       help='AIR service URL (e.g., https://example.com/air)')
    parser.add_argument('--token', required=True,
                       help='API authentication token')
    parser.add_argument('--tree',
                       help='Git tree name (e.g., netdev/net-next) [required for submission]')
    parser.add_argument('--branch',
                       help='Git branch name (optional)')
    parser.add_argument('--format', choices=['json', 'markup', 'inline'],
                       default='inline',
                       help='Review output format (default: inline)')
    parser.add_argument('--poll-interval', type=int, default=5,
                       help='Status polling interval in seconds (default: 5)')
    parser.add_argument('--no-wait', action='store_true',
                       help='Do not wait for review completion (submit or check once and exit)')
    parser.add_argument('--pw-series', type=int, metavar='SERIES_ID',
                       help='Patchwork series ID to review')
    parser.add_argument('--review-id', metavar='ID',
                       help='Existing review ID to check (skip submission)')
    parser.add_argument('patches', nargs='*', metavar='PATCH_FILE',
                       help='Patch files to submit')

    args = parser.parse_args()

    # Validate arguments
    if args.review_id:
        # Checking existing review - no tree or patches needed
        review_id = args.review_id
        is_new_submission = False
    else:
        # New submission - need tree and either patches or pw-series
        if not args.tree:
            parser.error('--tree is required when submitting new review')

        if args.pw_series and args.patches:
            parser.error('Cannot specify both --pw-series and patch files')

        if not args.pw_series and not args.patches:
            parser.error('Must specify either --pw-series or patch files')

        is_new_submission = True

    # Submit new review if needed
    if is_new_submission:
        if args.pw_series:
            print(f"Submitting patchwork series {args.pw_series} to {args.tree}...")
            review_id = submit_review(args.url, args.token, args.tree, args.branch,
                                     patchwork_series_id=args.pw_series)
        else:
            print(f"Reading {len(args.patches)} patch file(s)...")
            patches = read_patch_files(args.patches)
            print(f"Submitting to {args.tree}...")
            review_id = submit_review(args.url, args.token, args.tree, args.branch,
                                     patches=patches)

        print(f"Review ID: {review_id}")

        if args.no_wait:
            print("Submission complete (--no-wait specified)")
            return

        print(f"Monitoring status (polling every {args.poll_interval}s)...\n")
    else:
        # Checking existing review
        print(f"Checking review: {review_id}")
        review_id = args.review_id

    # Monitor status or check once
    if args.no_wait:
        # Check once and exit
        status = get_review_status(args.url, args.token, review_id)
        status_line = format_status_line(status)
        print(status_line)

        # If done, fetch and print results
        if status['status'] in ('done', 'error'):
            print("\nFetching review results...")
            final_status = get_review_status(args.url, args.token, review_id, fmt=args.format)
            reviews = final_status.get('review', [])
            patch_count = final_status.get('patch_count', 0)

            if reviews:
                print_reviews(reviews, patch_count)
            elif final_status['status'] == 'error':
                msg = final_status.get('message', 'Unknown error')
                print(f"Error: {msg}")
                sys.exit(1)

            if final_status['status'] == 'error':
                sys.exit(1)
    else:
        # Poll until complete
        last_line_len = 0
        try:
            while True:
                status = get_review_status(args.url, args.token, review_id)

                # Format and print status line (overwriting previous)
                status_line = format_status_line(status)
                # Clear previous line and print new status
                print(f"\r{' ' * last_line_len}\r{status_line}", end='', flush=True)
                last_line_len = len(status_line)

                # Check if done
                if status['status'] in ('done', 'error'):
                    print()  # New line after final status
                    break

                time.sleep(args.poll_interval)
        except KeyboardInterrupt:
            print(f"\n\nInterrupted by user. Review ID: {review_id}")
            sys.exit(1)

        # Fetch final results
        print("\nFetching review results...")
        final_status = get_review_status(args.url, args.token, review_id, fmt=args.format)

        reviews = final_status.get('review', [])
        patch_count = final_status.get('patch_count', 0)

        if not reviews:
            print("No reviews available")
            if final_status['status'] == 'error':
                msg = final_status.get('message', 'Unknown error')
                print(f"Error: {msg}")
                sys.exit(1)
        else:
            print_reviews(reviews, patch_count)

        # Exit with error if review failed
        if final_status['status'] == 'error':
            sys.exit(1)


if __name__ == '__main__':
    main()

# Night-Time

Calm static portal for quiet hours. Official YouTube embeds only (youtube-nocookie).

## Edit library
`data/curated.json` — favourites + categories + video IDs.

## Local Running

```bash
cd night-time
python3 -m http.server 5173
```

Or use a user systemd unit pointing at this directory when you want it persistent on this host only.

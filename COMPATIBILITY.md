# Compatibility Notes

## Overseerr / Jellyseerr / Seerr

This portal is compatible with **all** variants of the *seerr family of request management tools:

- ✅ **Overseerr** - The original (overseerr.dev)
- ✅ **Jellyseerr** - Fork optimized for Jellyfin  
- ✅ **Seerr** - The new unified fork

All three share the same API structure, so the portal works identically with any of them.

### Configuration

The environment variables use `JELLYSEERR_` prefix for historical reasons, but they work with any variant:

```env
JELLYSEERR_URL=http://your-seerr-instance:5055
JELLYSEERR_API_KEY=your_api_key
```

Just point the URL to whichever variant you're using - Overseerr, Jellyseerr, or Seerr!

### Webhook Setup

The webhook endpoint is the same regardless of which variant you use:

```
http://your-portal-ip:8000/webhook/jellyseerr
```

Configure it in:
- **Overseerr**: Settings → Notifications → Webhook
- **Jellyseerr**: Settings → Notifications → Webhook  
- **Seerr**: Settings → Notifications → Webhook

All three have identical webhook configurations.

## Why "Jellyseerr" in the code?

The portal was initially developed with Jellyseerr, so the internal naming stuck. Since all variants share the same API, it doesn't matter - think of it as "works with the seerr family" rather than being specific to one fork.

## Feature Support

All features work identically across variants:
- ✅ User sync
- ✅ Request sync  
- ✅ Webhook notifications
- ✅ Episode tracking
- ✅ Request status updates

Choose whichever seerr variant you prefer - they all work great! 🎯

package app.thirdeye.phone

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PairingLinkTest {

    @Test
    fun parsesValidLink() {
        val result = PairingLink.parse(
            "thirdeye://pair?url=" + java.net.URLEncoder.encode("http://100.1.2.3:8000", "UTF-8") +
                "&token=" + java.net.URLEncoder.encode("abc123", "UTF-8"))
        assertEquals("http://100.1.2.3:8000", result?.baseUrl)
        assertEquals("abc123", result?.token)
    }

    @Test
    fun rejectsWrongSchemeAndHost() {
        assertNull(PairingLink.parse("https://pair?url=http://x&token=t"))
        assertNull(PairingLink.parse("thirdeye://other?url=http://x&token=t"))
        assertNull(PairingLink.parse(null))
        assertNull(PairingLink.parse(""))
    }

    @Test
    fun rejectsMissingOrBadParams() {
        assertNull(PairingLink.parse("thirdeye://pair?url=http%3A%2F%2Fx"))
        assertNull(PairingLink.parse("thirdeye://pair?token=t"))
        assertNull(PairingLink.parse("thirdeye://pair?url=ftp%3A%2F%2Fx&token=t"))
        assertNull(PairingLink.parse("thirdeye://pair?url=&token=t"))
    }

    @Test
    fun decodesEncodedValues() {
        val result = PairingLink.parse(
            "thirdeye://pair?url=http%3A%2F%2F192.168.1.5%3A8000&token=a%20b%2Bc")
        assertEquals("http://192.168.1.5:8000", result?.baseUrl)
        assertEquals("a b+c", result?.token)
    }
}

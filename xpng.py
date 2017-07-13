#!/usr/bin/python
#
# xpng: a quick and dirty (and very buggy) PNG library
# 
# @author:Dominique Bongard
# 
#
# Code is licensed under -- Apache License 2.0 http://www.apache.org/licenses/
#
# Changes by floyd, @floyd_ch, https://www.floyd.ch


from collections import namedtuple
import struct
import zlib
import binascii
import itertools

class Xpng:

    # Named tuple representing a PNG chunk - offset is the position in the file
    Chunk = namedtuple("chunk", "size name content checksum offset")

    def __init__(self, content):
        self.content = content

        self.valid = 0

        self.chunks = None

        self.width = 0
        self.height = 0
        self.colorDepth = 0
        self.colorType = 0
        self.compressionMethod = 0
        self.filterMethod = 0
        self.interlaceMethod = 0

        self.filters_used = set()
        self.pixels = []
        self.zlevel = 0

        self._check_validity()

    # Private methods to setup this object

    def _check_validity(self):
        '''
        Reads the content and tries to decode it
        valid can take several values from 0 to 10 depending on how 'valid' the PNG file is
        valid == 0 => the file doesn't exist or is empty
        valid == 10 => the file is at least structurally correct
        '''
        if self.content:
            self.valid = 1
            if self.content.startswith('\x89PNG'):
                self.valid = 2
                try:
                    self._parse_chunks()
                    self.valid = 3
                    self._properties()
                    self.valid = 4
                    self._unfilter()
                    self.valid = 10
                except Exception, e:
                    # TODO: This was just a "pass"... Let's find out if this exception ever occurs at all
                    print e

    def _parse_chunks(self):
        'Parses all the chunks in the PNG file until it reaches IEND'
        self.chunks = []
        offset = 8
        chunk = Xpng.Chunk(0, "", 0, 0, 0)
        while chunk.name != "IEND":
            chunk = self._parse_chunk(self.content, offset)
            self.chunks.append(chunk)
            offset += chunk.size + 12

    def _parse_chunk(self, data, offset):
        'Gets binary data in input and returns a representation as a Chunk named tuple'
        start = offset
        size, name = struct.unpack_from("!I4s", data, start)
        start += 8
        content = data[start:start + size]
        start += size
        checksum = struct.unpack_from("!I", data, start)[0]
        return Xpng.Chunk(size, name, content, checksum, offset)

    def _chunk_checksum(self, name, content):
        'returns the crc32 of a chunk named tuple'
        return binascii.crc32(name + content) & 0xffffffff

    def _verify_checksum(self, chunk):
        'Returns True if the checksum of the passed Chunk is correct'
        return chunk.checksum == self._chunk_checksum(chunk.name, chunk.content)

    def _verify_checksums(self):
        'Returns True is the checksum of all the chunks in the image are correct'
        for chunk in self.chunks:
            if not self._verify_checksum(chunk):
                return False
        return True

    def _get_chunk(self, name, index=0):
        '''
        Returns a chunk which name corresponds to the name parameter.
        A PNG file can have several chunks with the same name, so there is also an index parameter
        '''
        currentIndex = 0
        for chunk in self.chunks:
            if chunk.name == name:
                if currentIndex == index:
                    return chunk
                else:
                    currentIndex += 1
        return None

    def _generate_chunk_blob(self, chunk):
        'Returns the binary representation of a Chunk named tuple'
        blob = struct.pack("!L4s", chunk.size, chunk.name)
        blob += chunk.content
        blob += struct.pack("!L", chunk.checksum)
        return blob

    def _get_chunk_blob(self, name, index=0):
        'Returns the binary representation of a Chunk named tuple given its name and index'
        chunk = self._get_chunk(name, index)
        if chunk == None:
            return None
        return self._generate_chunk_blob(chunk)

    def _properties(self):
        'Extracts the properties of the image from the ihdr chunk'
        ihdr = self._get_chunk('IHDR')
        self.width, self.height, self.colorDepth, self.colorType, self.compressionMethod, self.filterMethod, self.interlaceMethod = struct.unpack(
            "!IIBBBBB", ihdr.content)

    def _pixel_size(self):
        'Returns the size in bytes of a pixel, which depends on image type and bit depth'
        if self.colorType == 3:
            return 1
        else:
            size = [1, 0, 3, 1, 2, 0, 4]
            return (self.colorDepth / 8.0) * size[self.colorType]


    def _decompress(self):
        '''
        concatenates all the IDAT chunks and then decompresses the resulting zlib blob
        also extracts the zlib compression level
        '''
        finished = False
        compressed = ""
        index = 0
        while not finished:
            chunk = self._get_chunk('IDAT', index)
            if chunk == None:
                finished = True
            else:
                compressed += chunk.content
                index = index + 1
        self.zlevel = ord(compressed[1]) >> 6
        return bytearray(zlib.decompress(compressed))

    def _paeth(self, a, b, c):
        'paeth scanline compression filter'
        p = a + b - c
        pa = abs(p - a)
        pb = abs(p - b)
        pc = abs(p - c)
        if pa <= pb and pa <= pc:
            pr = a
        elif pb <= pc:
            pr = b
        else:
            pr = c
        return pr

    def _type0(self, a, b, c, x):
        'type 0 scanline compression filter'
        return list(x)

    def _type1(self, a, b, c, x):
        'type 1 scanline compression filter'
        return map(lambda k: (k[0] + k[1]) % 256, zip(a, x))

    def _type2(self, a, b, c, x):
        'type 2 scanline compression filter'
        return map(lambda k: (k[0] + k[1]) % 256, zip(b, x))

    def _type3(self, a, b, c, x):
        'type 3 scanline compression filter'
        return map(lambda k: (((k[0] + k[1]) // 2) + k[2]) % 256, zip(a, b, x))

    def _type4(self, a, b, c, x):
        'type 4 scanline compression filter'
        return map(lambda k: (self._paeth(k[0], k[1], k[2]) + k[3]) % 256, zip(a, b, c, x))

    def _unfilter_line(self, line, prior=None):
        '''
        Removes the PNG compression filter from a scanline
        A byte representing the compressed filter type is prepended to each scanline
        returns a list of pixels. Each pixel is a list of samples (e.g. [r,g,b])
        '''
        type, data = line[0], line[1:]
        # keep a list of the filters used by the compressor for fingerprinting purposes
        self.filters_used.add(type)
        ps = int(max(1, self._pixel_size()))  # pixel size for filtering purposes is always >= 1 byte
        unfiltered = []
        zeropixel = [0 for x in range(ps)]
        if prior == None:
            prior = [zeropixel for x in range(len(data) // ps)]

        a = zeropixel
        c = zeropixel

        filters = [self._type0, self._type1, self._type2, self._type3, self._type4]
        filter = filters[type]

        # Unfilter each pixel
        for i in range(len(data) // ps):
            x = list(data[i * ps:(i + 1) * ps])
            b = prior[i]
            recon = filter(a, b, c, x)
            a = recon
            c = b
            unfiltered.append(recon)
        return unfiltered

    def _unfilter(self):
        '''
        Unfilters the whole image
        The result self.pixels is a list of rows, containing a list of pixels containing a list of samples'
        '''
        prior = None
        ps = self._pixel_size()
        line_size = int(round(ps * self.width)) + 1
        filtered = self._decompress()
        for y in range(self.height):
            line = filtered[y * line_size:(y + 1) * (line_size)]
            unfiltered = self._unfilter_line(line, prior)
            self.pixels.append(unfiltered)
            prior = unfiltered


    # Public methods
    def getPaletteColors(self):
        '''
        Returns a list of all the colors in an indexed image
        It doesn't take into account if the color is actually used in the image
        '''
        plte = self._get_chunk("PLTE")
        plteBytes = bytearray(plte.content)
        colors = []
        for x in xrange(0, plte.size, 3):
            colors.append([plteBytes[x], plteBytes[x + 1], plteBytes[x + 2]])
        return colors


    def getPixelRgb(self, x, y):
        '''
        Returns the RGB value of a pixel in the image given its coordinates
        if the image is indexed, the pixel color is looked up in the palette
        alpha is discarded
        '''
        if not self.colorDepth == 8:
            return None
        value = self.pixels[y][x]
        if self.colorType == 2:
            return value
        elif self.colorType == 6:
            return value[0:3]
        elif self.colorType == 3:
            return self.getPaletteColors()[value[0]]

    def hasColor(self, color):
        'Check if the image contains a particular color'
        if not self.colorDepth == 8:
            return False
        if self.colorType == 2:
            return color in itertools.chain(*self.pixels)
        elif self.colorType == 6:
            return color in map(lambda x: [x[0], x[1], x[2]], itertools.chain(*self.pixels))
        elif self.colorType == 3:
            return color in self.getPaletteColors()

    def generateChunk(self, name, data):
        'Generate a chunk from name and data (for saving)'
        return Xpng.Chunk(len(data), name, data, self._chunk_checksum(name, data), 0)

    def generateIdat(self):
        'Generate the IDAT chunk from the pixels (for saving)'
        data = ""
        for line in self.pixels:
            data += '\0'
            data += str(bytearray(itertools.chain(*line)))
        compressed = zlib.compress(data)
        idat = self._generate_chunk_blob(self.generateChunk("IDAT", compressed))
        return idat

    def getBlob(self):
        'returns the binary representation of the image in PNG format'
        blob = "\x89PNG\x0d\x0a\x1a\x0a"
        blob += self._get_chunk_blob("IHDR")
        plte = self._get_chunk_blob("PLTE")
        if not plte == None:
            blob += plte
        blob += self.generateIdat()
        blob += self._get_chunk_blob("IEND")
        return blob

    def save(self, fileName):
        'Save the image in PNG format (used to verify that the image decoding works correctly)'
        with open(fileName, 'wb') as f:
            f.write(self.getBlob())

    ###############################################
    # Fingerprinting functions, referenced in tests.py
    ###############################################

    def conversionSuccess(self):
        '''
        The most simple fingerprinting function
        Returns 0 if the image is absent or empty (meaning the target failed to decode the input image)
        Returns 10 if the image looks valid at least in surface
        Returns between 1 and 9 if the image is corrupt
        '''
        return self.valid

    # All the following tests should return values > 10 (or any kind of object like a list actually)
    ################################################################################################

    def correctChecksums(self):
        'Fingerprint depending on the correctness of the checksums of the output image'
        if self._verify_checksums():
            return 11
        else:
            return 12

    def filtersUsed(self):
        'Fingerprint resulting from the set of filters used in the scanlines of the output image (returns a sorted list of the filters)'
        return sorted(self.filters_used)

    def paletteUsed(self):
        'Fingerprint depending on the palette used to decode images with two palettes (when not rejected)'
        if self.hasColor([185, 96, 142]):
            return 11
        elif self.hasColor([96, 142, 185]):
            return 12
        else:
            return 13

    def gamma(self):
        'Fingerprint depending on how the decoder treated the gamma information from the input image'
        pixel = self.getPixelRgb(120, 140)
        if pixel[0] + pixel[1] + pixel[2] < 96:
            return 11
        else:
            chunk = self._get_chunk("gAMA")
            if chunk == None:
                return 12
            gammav = struct.unpack("!I", chunk.content)
            if gammav[0] == 400000:
                return 13
            return 14

    def ihdrUsed(self):
        'Fingerprint depending on the ihdr used to decode images with two ihdr (when not rejected)'
        if self.width == 252:
            return 11
        elif self.width == 189:
            return 12
        else:
            return 13

    def badIdatFilter(self):
        'Fingerprint depending on the treatment of images with invalid scanline filters'
        pixel = self.getPixelRgb(5, 0)
        if pixel == [65, 83, 255]:
            return 11  # Most libraries return the correct image
        elif pixel == [57, 82, 255]:
            return 12  # One library outputs a corrupted image
        return 13

    def zlibCompression(self):
        'Fingerprint depending on the zlib compression level flag of the output image'
        return 11 + self.zlevel

    def physChunk(self):
        'Fingerprint depending on how the decoder treated the phys information in the input image'
        chunk = self._get_chunk("pHYs")
        if chunk == None:
            return 11
        x, y, u = struct.unpack("!IIB", chunk.content)
        if x == 1:
            return 12
        if x == 1500:
            return 13
        if x == 1499:
            return 14  # .net
        return 15

    def truecolorTrns(self):
        'Fingerprint depending on how the decoder treated an input image with a tRNS chunk'
        if self.colorType == 6:
            return 11
        chunk = self._get_chunk("tRNS")
        if chunk == None:
            return 12
        return 13

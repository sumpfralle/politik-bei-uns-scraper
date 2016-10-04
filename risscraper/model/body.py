"""
Copyright (c) 2012 - 2015, Ernesto Ruge
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

1. Redistributions of source code must retain the above copyright
   notice, this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright
   notice, this list of conditions and the following disclaimer in the
   documentation and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED
TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

from risscraper.model.base import Base


class Body(Base):
    """ A body class """

    def __init__(self, identifier=None, rgs=None, name=None, modified=None):
        # TODO: lots of variables ar missing - is "Body" still in use?
        self.identifier = identifier
        self.originalId = numericId
        self.originalUrl = originalUrl
        self.created = created
        self.modified = modified

        self.system = system
        self.shortName = shortName
        self.name = name
        self.website = website
        self.license = license
        self.licenseValidSince = licenseValidSince
        self.ags = ags
        self.rgs = rgs
        # list
        self.equivalentBody = equivalentBody
        self.contactEmail = contactEmail
        self.contactName = contactName
        # organization
        # person
        # meeting
        # agendaitem
        # paper
        # file
        # consultation
        # location
        # membership
        # legislativeTerm
        self.classification = classification
        super(Body, self).__init__()

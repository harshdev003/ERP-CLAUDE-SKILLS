/**
 * @module router
 * @description Express router for /coaching/notes
 * @copyright Aura Health, Inc.
 */

// External libraries
const express = require('express');

// Internal dependencies
const {
  ready,
  status,
  coachingNotes: {create, delete: deleteNote, list, update},
} = require('../../../handlers');
const {authenticate} = require('../../../middlewares');

const router = express.Router();

// Routes for the service status
router.get('/ready', ready);
router.get('/status', status);

// Users routes
// note: order is important
// @TODO: implement system to system security
router.delete('/:coachingNoteId', authenticate, deleteNote);

router.post('/list', authenticate, list);
router.post('/', authenticate, create);

router.put('/:coachingNoteId', authenticate, update);

module.exports = router;

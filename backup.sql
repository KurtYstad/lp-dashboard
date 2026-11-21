/*M!999999\- enable the sandbox mode */ 
-- MariaDB dump 10.19-11.8.6-MariaDB, for debian-linux-gnu (x86_64)
--
-- Host: localhost    Database: uniswap
-- ------------------------------------------------------
-- Server version	11.8.6-MariaDB-5ubuntu0.1 from Ubuntu

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*M!100616 SET @OLD_NOTE_VERBOSITY=@@NOTE_VERBOSITY, NOTE_VERBOSITY=0 */;

--
-- Current Database: `uniswap`
--

CREATE DATABASE /*!32312 IF NOT EXISTS*/ `uniswap` /*!40100 DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_uca1400_ai_ci */;

USE `uniswap`;

--
-- Table structure for table `aero`
--

DROP TABLE IF EXISTS `aero`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `aero` (
  `uid` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `id` int(7) unsigned NOT NULL,
  `date` datetime NOT NULL,
  `token_0` varchar(100) NOT NULL,
  `token_1` varchar(100) NOT NULL,
  `symbol_0` varchar(10) NOT NULL,
  `symbol_1` varchar(10) NOT NULL,
  `fee_0` decimal(25,18) NOT NULL,
  `fee_1` decimal(25,18) NOT NULL,
  `quote_0` decimal(25,18) NOT NULL,
  `quote_1` decimal(25,18) NOT NULL,
  `current_price` decimal(25,18) NOT NULL,
  `in_range` tinyint(1) unsigned NOT NULL,
  `reward_token` varchar(42) DEFAULT NULL,
  `reward_symbol` varchar(32) DEFAULT NULL,
  `reward_amount` decimal(65,30) NOT NULL DEFAULT 0.000000000000000000000000000000,
  `reward_quote` decimal(65,30) NOT NULL DEFAULT 0.000000000000000000000000000000,
  PRIMARY KEY (`uid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--

--
-- Table structure for table `arbitrum`
--

DROP TABLE IF EXISTS `arbitrum`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `arbitrum` (
  `uid` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `id` int(7) unsigned NOT NULL,
  `date` datetime NOT NULL,
  `token_0` varchar(100) NOT NULL,
  `token_1` varchar(100) NOT NULL,
  `symbol_0` varchar(10) NOT NULL,
  `symbol_1` varchar(10) NOT NULL,
  `fee_0` decimal(25,18) NOT NULL,
  `fee_1` decimal(25,18) NOT NULL,
  `quote_0` decimal(25,18) NOT NULL,
  `quote_1` decimal(25,18) NOT NULL,
  `current_price` decimal(25,18) NOT NULL,
  `in_range` tinyint(1) unsigned NOT NULL,
  PRIMARY KEY (`uid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--

--
-- Table structure for table `base`
--

DROP TABLE IF EXISTS `base`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `base` (
  `uid` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `id` int(7) unsigned NOT NULL,
  `date` datetime NOT NULL,
  `token_0` varchar(100) NOT NULL,
  `token_1` varchar(100) NOT NULL,
  `symbol_0` varchar(10) NOT NULL,
  `symbol_1` varchar(10) NOT NULL,
  `fee_0` decimal(25,18) NOT NULL,
  `fee_1` decimal(25,18) NOT NULL,
  `quote_0` decimal(25,18) NOT NULL,
  `quote_1` decimal(25,18) NOT NULL,
  `current_price` decimal(25,18) NOT NULL,
  `in_range` tinyint(1) unsigned NOT NULL,
  PRIMARY KEY (`uid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--

--
-- Table structure for table `bsc`
--

DROP TABLE IF EXISTS `bsc`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `bsc` (
  `uid` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `id` int(7) unsigned NOT NULL,
  `date` datetime NOT NULL,
  `token_0` varchar(100) NOT NULL,
  `token_1` varchar(100) NOT NULL,
  `symbol_0` varchar(10) NOT NULL,
  `symbol_1` varchar(10) NOT NULL,
  `fee_0` decimal(25,18) NOT NULL,
  `fee_1` decimal(25,18) NOT NULL,
  `quote_0` decimal(25,18) NOT NULL,
  `quote_1` decimal(25,18) NOT NULL,
  `current_price` decimal(25,18) NOT NULL,
  `in_range` tinyint(1) unsigned NOT NULL,
  PRIMARY KEY (`uid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--

--
-- Table structure for table `cake`
--

DROP TABLE IF EXISTS `cake`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `cake` (
  `uid` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `id` int(7) unsigned NOT NULL,
  `date` datetime NOT NULL,
  `token_0` varchar(100) NOT NULL,
  `token_1` varchar(100) NOT NULL,
  `token_2` varchar(100) NOT NULL,
  `symbol_0` varchar(10) NOT NULL,
  `symbol_1` varchar(10) NOT NULL,
  `symbol_2` varchar(10) NOT NULL,
  `fee_0` decimal(25,18) NOT NULL,
  `fee_1` decimal(25,18) NOT NULL,
  `fee_2` decimal(25,18) NOT NULL,
  `quote_0` decimal(25,18) NOT NULL,
  `quote_1` decimal(25,18) NOT NULL,
  `quote_2` decimal(25,18) NOT NULL,
  `current_price` decimal(25,18) NOT NULL,
  `in_range` tinyint(1) unsigned NOT NULL,
  PRIMARY KEY (`uid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--

--
-- Table structure for table `ethereum`
--

DROP TABLE IF EXISTS `ethereum`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `ethereum` (
  `uid` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `id` int(7) unsigned NOT NULL,
  `date` datetime NOT NULL,
  `token_0` varchar(100) NOT NULL,
  `token_1` varchar(100) NOT NULL,
  `symbol_0` varchar(10) NOT NULL,
  `symbol_1` varchar(10) NOT NULL,
  `fee_0` decimal(25,18) NOT NULL,
  `fee_1` decimal(25,18) NOT NULL,
  `quote_0` decimal(25,18) NOT NULL,
  `quote_1` decimal(25,18) NOT NULL,
  `current_price` decimal(25,18) NOT NULL,
  `in_range` tinyint(1) unsigned NOT NULL,
  PRIMARY KEY (`uid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--

--
-- Table structure for table `optimism`
--

DROP TABLE IF EXISTS `optimism`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `optimism` (
  `uid` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `id` int(7) unsigned NOT NULL,
  `date` datetime NOT NULL,
  `token_0` varchar(100) NOT NULL,
  `token_1` varchar(100) NOT NULL,
  `symbol_0` varchar(10) NOT NULL,
  `symbol_1` varchar(10) NOT NULL,
  `fee_0` decimal(25,18) NOT NULL,
  `fee_1` decimal(25,18) NOT NULL,
  `quote_0` decimal(25,18) NOT NULL,
  `quote_1` decimal(25,18) NOT NULL,
  `current_price` decimal(25,18) NOT NULL,
  `in_range` tinyint(1) unsigned NOT NULL,
  PRIMARY KEY (`uid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--

--
-- Table structure for table `orca`
--

DROP TABLE IF EXISTS `orca`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `orca` (
  `uid` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `id` varchar(44) NOT NULL,
  `date` datetime NOT NULL,
  `token_0` varchar(100) NOT NULL,
  `token_1` varchar(100) NOT NULL,
  `symbol_0` varchar(10) NOT NULL,
  `symbol_1` varchar(10) NOT NULL,
  `fee_0` decimal(25,18) NOT NULL,
  `fee_1` decimal(25,18) NOT NULL,
  `quote_0` decimal(25,18) NOT NULL,
  `quote_1` decimal(25,18) NOT NULL,
  `current_price` decimal(25,18) NOT NULL,
  `in_range` tinyint(1) unsigned NOT NULL,
  PRIMARY KEY (`uid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--

--
-- Table structure for table `polygon`
--

DROP TABLE IF EXISTS `polygon`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `polygon` (
  `uid` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `id` int(7) unsigned NOT NULL,
  `date` datetime NOT NULL,
  `token_0` varchar(100) NOT NULL,
  `token_1` varchar(100) NOT NULL,
  `symbol_0` varchar(10) NOT NULL,
  `symbol_1` varchar(10) NOT NULL,
  `fee_0` decimal(25,18) NOT NULL,
  `fee_1` decimal(25,18) NOT NULL,
  `quote_0` decimal(25,18) NOT NULL,
  `quote_1` decimal(25,18) NOT NULL,
  `current_price` decimal(25,18) NOT NULL,
  `in_range` tinyint(1) unsigned NOT NULL,
  PRIMARY KEY (`uid`),
  KEY `id` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--

--
-- Table structure for table `positions`
--

DROP TABLE IF EXISTS `positions`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `positions` (
  `uid` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `id` varchar(128) NOT NULL,
  `date` datetime(6) DEFAULT NULL,
  `protocol` varchar(32) NOT NULL,
  `blockchain` varchar(24) NOT NULL,
  `token_0` varchar(128) NOT NULL,
  `symbol_0` varchar(64) NOT NULL,
  `amount_0` decimal(65,30) DEFAULT NULL,
  `quote_usd_0` decimal(38,18) DEFAULT NULL,
  `quote_eur_0` decimal(38,18) DEFAULT NULL,
  `token_1` varchar(128) NOT NULL,
  `symbol_1` varchar(64) NOT NULL,
  `amount_1` decimal(65,30) DEFAULT NULL,
  `quote_usd_1` decimal(38,18) DEFAULT NULL,
  `quote_eur_1` decimal(38,18) DEFAULT NULL,
  `source` varchar(20) NOT NULL DEFAULT 'internal',
  `source_table` varchar(32) NOT NULL,
  `position_contract` varchar(66) NOT NULL DEFAULT 'unknown',
  `position_account` varchar(128) DEFAULT NULL,
  `position_version` varchar(16) DEFAULT NULL,
  `mint_tx` varchar(128) DEFAULT NULL,
  `mint_block` bigint(20) unsigned DEFAULT NULL,
  `mint_slot` bigint(20) unsigned DEFAULT NULL,
  `deposit_tx` varchar(128) DEFAULT NULL,
  `deposit_date` datetime(6) DEFAULT NULL,
  `amount_source` varchar(40) DEFAULT NULL,
  `quote_timestamp` datetime(6) DEFAULT NULL,
  `fx_source` varchar(24) DEFAULT NULL,
  `fx_date` date DEFAULT NULL,
  `entry_value_usd` decimal(65,30) DEFAULT NULL,
  `entry_value_eur` decimal(65,30) DEFAULT NULL,
  `status` varchar(16) NOT NULL DEFAULT 'pending',
  `last_error` text DEFAULT NULL,
  `updated_at` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`uid`),
  UNIQUE KEY `uq_positions_source_id_contract` (`source_table`,`protocol`,`blockchain`,`id`,`position_contract`,`token_0`,`token_1`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--

--
-- Table structure for table `raydium`
--

DROP TABLE IF EXISTS `raydium`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `raydium` (
  `uid` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `id` varchar(44) NOT NULL,
  `date` datetime NOT NULL,
  `token_0` varchar(100) NOT NULL,
  `token_1` varchar(100) NOT NULL,
  `symbol_0` varchar(10) NOT NULL,
  `symbol_1` varchar(10) NOT NULL,
  `fee_0` decimal(25,18) NOT NULL,
  `fee_1` decimal(25,18) NOT NULL,
  `quote_0` decimal(25,18) NOT NULL,
  `quote_1` decimal(25,18) NOT NULL,
  `current_price` decimal(25,18) NOT NULL,
  `in_range` tinyint(1) unsigned NOT NULL,
  PRIMARY KEY (`uid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--

--
-- Table structure for table `robinhood`
--

DROP TABLE IF EXISTS `robinhood`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `robinhood` (
  `uid` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `id` int(7) unsigned NOT NULL,
  `date` datetime NOT NULL,
  `token_0` varchar(100) NOT NULL,
  `token_1` varchar(100) NOT NULL,
  `symbol_0` varchar(10) NOT NULL,
  `symbol_1` varchar(10) NOT NULL,
  `fee_0` decimal(25,18) NOT NULL,
  `fee_1` decimal(25,18) NOT NULL,
  `quote_0` decimal(25,18) NOT NULL,
  `quote_1` decimal(25,18) NOT NULL,
  `current_price` decimal(25,18) NOT NULL,
  `in_range` tinyint(1) unsigned NOT NULL,
  PRIMARY KEY (`uid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--

--
-- Table structure for table `total`
--

DROP TABLE IF EXISTS `total`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `total` (
  `uid` int(11) NOT NULL AUTO_INCREMENT,
  `pos_id` varchar(44) NOT NULL,
  `timestamp` datetime NOT NULL,
  `token` varchar(100) NOT NULL,
  `symbol` varchar(10) NOT NULL,
  `fee` decimal(25,18) NOT NULL,
  `chain` varchar(10) NOT NULL,
  PRIMARY KEY (`uid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--

--
-- Dumping events for database 'uniswap'
--

--
-- Dumping routines for database 'uniswap'
--
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*M!100616 SET NOTE_VERBOSITY=@OLD_NOTE_VERBOSITY */;

-- Dump completed on 2026-11-21 19:11:22

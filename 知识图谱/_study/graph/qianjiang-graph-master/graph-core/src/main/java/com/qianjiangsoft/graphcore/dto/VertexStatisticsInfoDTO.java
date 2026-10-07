package com.qianjiangsoft.graphcore.dto;

import lombok.Data;

/**
 * @author marshallwu
 * @date 2022/2/24 11:46
 */

@Data
public class VertexStatisticsInfoDTO {
    Long outDegree;
    Long inDegree;
    Long allDegree;

    Long inCount;
    Long outCount;
    Long allCount;

    Double inStrength;
    Double outStrength;
    Double allStrength;
}

package com.qianjiangsoft.graphcore.constants;

/**
 * @author marshallwu
 * @date 2022/4/29 13:54
 */
public enum StatisticsInfoType {

    strength("强度"), count("次数"), outDegree("出度"), inDegree("入度"), totalDegree("总度数");

    public final String name;

    StatisticsInfoType(String name) {
        this.name = name;
    }
}

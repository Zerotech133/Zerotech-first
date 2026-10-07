package com.qianjiangsoft.graphcore.constants;

/**
 * @author marshallwu
 * @date 2022/4/29 13:54
 */
public enum SortingDirection {

    asc("升序"), desc("降序");

    public final String name;

    SortingDirection(String name) {
        this.name = name;
    }
}

package com.qianjiangsoft.graphcore.param;

import com.qianjiangsoft.graphcore.constants.SearchMode;
import lombok.Data;

/**
 * @author marshallwu
 * @date 2022/4/29 14:31
 */
@Data
public class TextSearchParam {

    SearchMode mode;

    String keyword;
}

package com.qianjiangsoft.graphapi.controller;

import com.qianjiangsoft.graphapi.dto.SpaceInfoListDTO;
import com.qianjiangsoft.graphapi.param.CreateSpaceDTO;
import com.qianjiangsoft.graphapi.service.SpaceInfoService;
import com.qianjiangsoft.graphcore.page.PageDataVO;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import javax.servlet.http.HttpServletRequest;

/**
 * @author ulmarr8861
 * @date 2022/3/16
 */
@RestController
@RequestMapping(value = "/space")
@RequiredArgsConstructor
public class SpaceController {

    private final SpaceInfoService spaceInfoService;

    @PostMapping("/create")
    public CreateSpaceDTO createSpace(String id, String name) {
        return spaceInfoService.createSpace(id, name);
    }

    @PostMapping("/delete")
    public void deleteSpace(@RequestParam("id") String id) {
        spaceInfoService.deleteSpace(id);
    }

    @GetMapping("/selectPage")
    public PageDataVO<SpaceInfoListDTO> selectListByPage() {
        return spaceInfoService.selectSpaceListByPagePage();
    }


    @GetMapping("/checkSpaceStatus")
    public void checkSpaceStatus(HttpServletRequest request) {
        String spaceId = request.getHeader("spaceId");
        spaceInfoService.checkSpaceStatus(spaceId);
    }
}
